"""Compose `hive serve`: the Hive `hive run` builds, plus the Hive Entrance, in one process.

`hive serve` runs the Queen (the central orchestrator), her Warden and the Hive Entrance (the Hive's
one HTTP door) in one event loop (ADR-0032). `build_served_hive` builds the Hive exactly as
`build_hive` does (its Queen's `HumanChannel` is a relay, bound to the Entrance's push channel once
that exists); `serve_hive` then, holding the Hive's serve lock for its whole life (one serve per
Hive, never beside an offline `hive entrance` step: `hivemind.cli.entrance.serving`), in the
running loop, checks `[entrance]` against this host
(`gather_facts`, then `plan_exposure`, which refuses a mode whose prerequisites do not hold), binds
the loopback listener's socket (a loopback listener that cannot bind refuses to start), opens the
Entrance's tables on the Hive's own `[hive] db` file, loads the keys from the secret store (the
Hive's Ed25519 key, the Web Push keys, and in a remote mode the Hive's certificate authority),
builds the Entrance, and runs it inside `run_hive` until the caller leaves the block, publishing
the serve record (the loopback listener's port) the console commands find it by. The Web Push
contact defaults to `[entrance] public_url` when `HIVEMIND_ENTRANCE_VAPID_SUBJECT` is unset; with
neither, Web Push is not offered. The tunnel child's environment is the Hive's without its own
variables and without any provider's API key, plus `HIVEMIND_ENTRANCE_TUNNEL_*`. While
`[entrance.voice]` is on (roadmap step 10.5f), the `TRANSCRIBER` slot is bound through the Hive's
own provider registry and metered by its own Fanner (`bind_transcriber`), so every clip is one
`llm.call` on the Hive's trail, and a slot bound to a provider that cannot transcribe refuses to
start `hive serve` rather than failing the first spoken word; the transcript is scored by the
Queen's own scanner, and a kept clip goes to the in-memory Nectar seam until phase 7.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.serve` and by the
    end-to-end tests. Calls into `hivemind.cli.compose.hive`, `hivemind.cli.entrance.serving`,
    `hivemind.entrance`, the secret store and the manifest.

Key invariants:
    - Nothing listens before the serve lock is held, the exposure plan holds and the loopback
      socket is bound.
    - The Entrance stops (every socket closed, both listeners down) before the Queen does.
    - Voice is wired only while `[entrance.voice]` is on; off, its route is never mounted.

See Also:
    - hivemind.entrance.runtime for what is built here.
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for exposure.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from hivemind.cli.compose.hive import Hive, build_hive, run_hive
from hivemind.cli.entrance.serving import (
    SERVE_HOLDER,
    ServeRecord,
    hold_serve_lock,
    publish_serve_record,
)
from hivemind.common.logging import get_logger
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.common.sqlite import connect
from hivemind.entrance.enrol import EntranceIdentity
from hivemind.entrance.expose import (
    ExposurePlan,
    LocalInterfaces,
    SystemInterfaces,
    gather_facts,
    load_or_create_authority,
    plan_exposure,
    tunnel_environment,
)
from hivemind.entrance.gate import HiveReads, LlmReads
from hivemind.entrance.push import (
    Resolver,
    SqliteSubscriptionStore,
    VapidSigner,
    load_or_mint_topic_key,
    load_or_mint_vapid_key,
    system_resolver,
)
from hivemind.entrance.runtime import (
    EntranceHive,
    EntranceKeys,
    EntranceParts,
    EntranceSettings,
    EntranceTables,
    HiveEntrance,
    IPAddress,
    bind_listener,
    build_entrance,
)
from hivemind.entrance.store import SqliteEntranceStore
from hivemind.entrance.voice import InMemoryAudioNectar, VoiceRules, VoiceServices
from hivemind.forage.tempo import AccuracyBar, Tempo
from hivemind.llm import Responder, bind_transcriber
from hivemind.manifest import EnvOverrides, HiveManifest, read_env
from hivemind.manifest.schema.entrance import split_host_port
from waggle.clock import Clock

OBSERVATION_BUILD = Path(
    "packages/observation-web/dist"
)  # The front end's build, manifest-relative.
PUSH_HTTP_TIMEOUT_S = 10.0  # One push delivery's whole request; each channel also bounds its own.
ENTRANCE_ACTOR = "system"  # What the Entrance process records as when no device decided.
# A person waits at the door for the echo of what they said: the Fanner orders a clip's seat
# queue (and spills, where a chain has a fallback) by this budget, at an ordinary accuracy bar.
VOICE_TEMPO = Tempo(latency_budget_s=30.0, accuracy=AccuracyBar.NORMAL)

log = get_logger(__name__)

__all__ = ["OBSERVATION_BUILD", "VOICE_TEMPO", "ServedHive", "build_served_hive", "serve_hive"]


@dataclass(frozen=True, slots=True)
class ServedHive:
    """A Hive built for `hive serve`, not yet started.

    Attributes:
        hive: The Hive `build_hive` built; `serve_hive` binds its `human_channel` relay to the
            Entrance's push channel.
        environ: The composition root's environment: the Web Push key and contact, the tunnel's.
        interfaces: This host's interfaces; the kernel's unless a test injects its own.
        resolver: Resolves push destinations; the system resolver unless a test injects one.
    """

    hive: Hive
    environ: Mapping[str, str] = field(repr=False)
    interfaces: LocalInterfaces = field(default_factory=SystemInterfaces)
    resolver: Resolver = system_resolver


def build_served_hive(
    manifest: HiveManifest,
    *,
    environ: Mapping[str, str],
    clock: Clock,
    responders: Mapping[str, Responder] | None = None,
) -> ServedHive:
    """Build the Hive `hive serve` runs: exactly `build_hive`'s, not yet started.

    Must run outside any event loop, like `build_hive` itself.

    Args:
        manifest: A HiveManifest loaded by `hivemind.manifest.load_manifest`.
        environ: The composition root's environment mapping.
        clock: The time source every collaborator shares.
        responders: Installed on every `kind = "fake"` provider; None in production.

    Returns:
        The Hive, not yet started.
    """
    hive = build_hive(manifest, environ=environ, clock=clock, responders=responders)
    return ServedHive(hive=hive, environ=environ)


@asynccontextmanager
async def serve_hive(served: ServedHive) -> AsyncIterator[HiveEntrance]:
    """Plan, bind, build and run the Entrance beside the Queen until the block exits.

    Holds the Hive's serve lock throughout and, once the loopback listener serves, publishes the
    serve record the Hive Stand's console commands find it by (`hivemind.cli.entrance.serving`).

    Args:
        served: The Hive `build_served_hive` built.

    Yields:
        The running Entrance, its listeners serving.

    Raises:
        HiveBusyError: Another `hive serve`, or an offline `hive entrance` step, holds the Hive.
        ExposureRefusedError: `[entrance]` asks for a mode this host cannot honour.
        OSError: The loopback listener could not bind.
        TranscriptionUnsupportedError: Voice is on and `[llm.slots.transcriber]` (or a
            fallback) names a provider that cannot transcribe.
    """
    manifest = served.hive.manifest
    db = manifest.resolve_path(manifest.hive.db)
    # One serve per Hive, and never beside an offline step that rewrites what a serve holds.
    with hold_serve_lock(db, SERVE_HOLDER):
        async with _serve_held(served) as entrance:
            record = ServeRecord(
                pid=os.getpid(),
                host=split_host_port(manifest.entrance.bind)[0],
                port=entrance.listeners.loopback_port,
                started_at=served.hive.clock.now(),
            )
            with publish_serve_record(db, record):
                yield entrance


@asynccontextmanager
async def _serve_held(served: ServedHive) -> AsyncIterator[HiveEntrance]:
    """Plan, bind, build and run the Entrance beside the Queen, the serve lock already held."""
    hive = served.hive
    manifest, clock = hive.manifest, hive.clock
    plan = await _plan(served)
    # Bound before anything listens, so a transcriber that cannot be bound refuses to start.
    voice = _voice(hive)
    loopback = bind_listener(plan.loopback.host, plan.loopback.port)
    async with AsyncExitStack() as stack:
        # Closed on every way out, a failed build included; closing it twice is harmless.
        stack.callback(loopback.close)
        tables = await _open_tables(manifest, hive, clock)
        keys = await _load_keys(manifest, read_env(served.environ), plan, clock)
        # No environment proxy: the destination guard's pinned address is where a push connects.
        http = await stack.enter_async_context(
            httpx.AsyncClient(trust_env=False, timeout=PUSH_HTTP_TIMEOUT_S)
        )
        parts = EntranceParts(
            settings=_settings(served, plan),
            tables=tables,
            hive=_entrance_hive(hive),
            keys=keys,
            clock=clock,
            http=http,
            own_addresses=await _own_addresses(served, plan),
            resolver=served.resolver,
            voice=voice,
        )
        built = build_entrance(parts, loopback)
        # From here on the Queen's calls reach the human's devices.
        hive.human_channel.bind(built.human_channel)
        async with run_hive(hive), _running(built.entrance) as entrance:
            yield entrance


@asynccontextmanager
async def _running(entrance: HiveEntrance) -> AsyncIterator[HiveEntrance]:
    """Run the Entrance in its own task group; stop it (sockets, then listeners) on exit."""
    async with asyncio.TaskGroup() as group:
        group.create_task(entrance.run())
        try:
            # Should run() fail first, the group cancels this wait and raises its error on exit.
            await entrance.wait_running()
            yield entrance
        finally:
            await entrance.stop()


async def _plan(served: ServedHive) -> ExposurePlan:
    """Check `[entrance]` against this host and plan the listeners, or refuse."""
    manifest = served.hive.manifest
    facts = await gather_facts(
        manifest.entrance, served.interfaces, served.hive.clock, manifest.resolve_path
    )
    return plan_exposure(manifest.entrance, facts)


async def _open_tables(manifest: HiveManifest, hive: Hive, clock: Clock) -> EntranceTables:
    """Open the Entrance's and the push channel's tables on the Hive's own database file."""
    db = manifest.resolve_path(manifest.hive.db)
    # Separate connections to one file, like every other store (ADR-0006); WAL makes that fine.
    store = await SqliteEntranceStore.create(connect(db), clock)
    push = await SqliteSubscriptionStore.create(connect(db), clock)
    return EntranceTables(store=store, push=push, trail=hive.stores.trail)


async def _load_keys(
    manifest: HiveManifest, env: EnvOverrides, plan: ExposurePlan, clock: Clock
) -> EntranceKeys:
    """Load (or, on the first serve, mint) every key the Entrance signs or serves TLS with."""
    store = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    signer = await load_or_mint_hive_signer(store)
    vapid = None
    subject = env.entrance_vapid_subject or manifest.entrance.public_url
    if manifest.entrance.push.web_push and subject:
        key = await load_or_mint_vapid_key(store, env.entrance_vapid_private_key)
        vapid = VapidSigner(key, subject, clock)
    elif manifest.entrance.push.web_push:
        # Push services need a contact; without one Web Push is simply not offered.
        log.warning("entrance.web_push_unavailable", reason="no_vapid_subject")
    # The remote listener's TLS context is built over the authority in every remote mode.
    authority = (
        await load_or_create_authority(store, manifest.hive.id, clock.now())
        if plan.remote is not None
        else None
    )
    return EntranceKeys(
        hive_signer=signer,
        vapid=vapid,
        topic_key=await load_or_mint_topic_key(store),
        authority=authority,
    )


def _settings(served: ServedHive, plan: ExposurePlan) -> EntranceSettings:
    """Read what the manifest decides for the Entrance."""
    manifest = served.hive.manifest
    web_root = manifest.resolve_path(OBSERVATION_BUILD)
    tunnel_env = None
    if plan.tunnel_argv:
        # The child never inherits a provider's API key or any HIVEMIND_ variable.
        withheld = [
            spec.api_key_env
            for spec in manifest.llm.providers.values()
            if spec.api_key_env is not None
        ]
        overrides = read_env(served.environ)
        tunnel_env = tunnel_environment(served.environ, overrides.entrance_tunnel_environ, withheld)
    return EntranceSettings(
        section=manifest.entrance,
        identity=EntranceIdentity(
            hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=ENTRANCE_ACTOR
        ),
        goal_spend_cap_usd=manifest.forage.spend_cap_per_goal_usd,
        plan=plan,
        web_root=web_root if web_root.is_dir() else None,
        tunnel_env=tunnel_env,
    )


def _entrance_hive(hive: Hive) -> EntranceHive:
    """Hand the Entrance the Queen's door, what it reads directly, and the Hive's one enforcer."""
    stores, deps = hive.stores, hive.queen_deps
    # Providers by name and kind only: the view never shows a key, its variable or a base URL.
    providers = {name: spec.kind for name, spec in hive.manifest.llm.providers.items()}
    llm = LlmReads(
        providers=providers,
        bindings=deps.bindings,
        cluster=deps.cluster_state,
        health=deps.health_poller,
    )
    virtual = hive.virtual_cells
    reads = HiveReads(
        goal_requests=stores.goal_requests,
        chat=stores.chat,
        chamber=stores.chamber,
        trail=stores.trail,
        memory=stores.memory,
        ledger=deps.ledger,
        census=hive.queen,  # Her Wardens and their pulse, read-only (HiveCensus).
        telemetry=hive.telemetry,
        llm=llm,
        virtual_cells=virtual.lifecycle if virtual is not None else None,
    )
    return EntranceHive(
        queen=hive.queen, reads=reads, enforcer=hive.enforcer, policy=hive.enforcer.policy
    )


def _voice(hive: Hive) -> VoiceServices | None:
    """Bind and meter the TRANSCRIBER slot for voice at the Entrance; None while voice is off."""
    section = hive.manifest.entrance.voice
    if not section.enabled:
        return None
    return VoiceServices(
        transcriber=bind_transcriber(hive.registry, hive.fanner, VOICE_TEMPO),
        scanner=hive.queen_deps.scanner,
        nectar=InMemoryAudioNectar(),
        rules=VoiceRules.from_section(section),
    )


async def _own_addresses(served: ServedHive, plan: ExposurePlan) -> frozenset[IPAddress]:
    """Every address the Hive Stand answers on: no push destination may be one of them."""
    snapshot = await served.interfaces.snapshot()
    addresses: set[IPAddress] = {
        address for interface in snapshot for address in interface.addresses
    }
    # The remote listener's own address too, in case no interface reported it.
    if plan.remote is not None:
        try:
            addresses.add(ipaddress.ip_address(plan.remote.host))
        except ValueError:
            log.debug("entrance.remote_host_not_an_address")
    return frozenset(addresses)
