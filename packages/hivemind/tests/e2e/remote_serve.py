"""Serve `hive serve`'s Hive with a remote listener too, under the serving rig's test-only vpn plan.

A phone reaches the Hive Entrance (the Hive's one HTTP door) through its remote listener, which
`hive serve` opens only in a remote mode, on an overlay (VPN) address with TLS on a DNS name
(ADR-0033): `plan_exposure` refuses anything less, rightly, and a test host has no overlay interface
to bind. `serve_exposed` composes exactly what `serve_hive` composes over the Hive
`build_served_hive` built (a real Queen, the orchestrator; her Warden, supervising the Hive Stand,
the machine she runs on; a Drone, the worker bee doing the task; the Hive's own SQLite file and
secret store; the push transport and resolver its `ServedHive` carries; the Hive Stand's own
addresses barred as push destinations), but hands `build_entrance` the plan the builders' serving
rig uses instead of one planned from the manifest: `vpn` mode with its public origin and relying
party (`hive.example.ts.net`), the remote listener on a second loopback port, plain HTTP. Everything
else is the Entrance's own: the remote application has no loopback-only route, its own passkey
relying party and origin, and sessions opened on it work nowhere else. Web Push is offered with the
rig's VAPID contact; the serve lock and record are left out, since no `hive` command looks for this
serve.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Handed to
    `e2e.entrance_stand.standing` by the phone test of phase 10's third exit criterion.

Key invariants:
    - The Entrance stops (sockets, then listeners) before the Queen does, as under `serve_hive`.
    - The plan differs from a real vpn plan only in where the remote listener binds and its TLS.

See Also:
    - hivemind.cli.compose.entrance for `serve_hive`, whose composition this mirrors.
    - builders.entrance.serving for the rig whose plan, origin and relying party this reuses.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from builders.entrance.serving import PUBLIC_ORIGIN, REMOTE_RP_ID, VAPID_SUBJECT

from hivemind.cli.compose import Hive, run_hive
from hivemind.cli.compose.entrance import ServedHive
from hivemind.common.secrets import FileSecretStore, load_or_mint_hive_signer
from hivemind.common.sqlite import connect
from hivemind.entrance.enrol import EntranceIdentity
from hivemind.entrance.expose import ExposurePlan, ListenerPlan
from hivemind.entrance.gate import HiveReads, LlmReads
from hivemind.entrance.push import (
    SqliteSubscriptionStore,
    VapidSigner,
    load_or_mint_topic_key,
    load_or_mint_vapid_key,
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
from hivemind.manifest import EntranceExposure

LOOPBACK_HOST = "127.0.0.1"  # Where both listeners bind: the remote one stands in for the overlay.
PUSH_HTTP_TIMEOUT_S = 10.0  # One push delivery's whole request, as `hive serve` bounds it.
ENTRANCE_ACTOR = "system"  # What the Entrance records as when no device decided, as in hive serve.

__all__ = ["LOOPBACK_HOST", "serve_exposed"]


@asynccontextmanager
async def serve_exposed(served: ServedHive) -> AsyncIterator[HiveEntrance]:
    """Build and run the Entrance, both listeners serving, beside the running Queen.

    Args:
        served: The Hive `build_served_hive` built, not yet started.

    Yields:
        The running Entrance: `listeners.remote_port` is the phone's way in.
    """
    hive = served.hive
    loopback = bind_listener(LOOPBACK_HOST, 0)
    async with AsyncExitStack() as stack:
        # Closed on every way out, a failed build included; closing it twice is harmless.
        stack.callback(loopback.close)
        # No environment proxy; the transport is the test's recorder, as `ServedHive` carries it.
        http = await stack.enter_async_context(
            httpx.AsyncClient(
                trust_env=False, timeout=PUSH_HTTP_TIMEOUT_S, transport=served.push_transport
            )
        )
        parts = EntranceParts(
            settings=_settings(hive),
            tables=await _tables(hive),
            hive=_entrance_hive(hive),
            keys=await _keys(hive),
            clock=hive.clock,
            http=http,
            own_addresses=await _own_addresses(served),
            resolver=served.resolver,
        )
        built = build_entrance(parts, loopback)
        # From here on the Queen's questions reach the human's devices, as under `serve_hive`.
        hive.human_channel.bind(built.human_channel)
        async with run_hive(hive), _running(built.entrance) as entrance:
            yield entrance


def _settings(hive: Hive) -> EntranceSettings:
    """The manifest's `[entrance]` section, under the rig's vpn plan instead of a planned one."""
    manifest = hive.manifest
    listener = ListenerPlan(LOOPBACK_HOST, 0, None)
    plan = ExposurePlan(
        mode=EntranceExposure.VPN,
        loopback=listener,
        remote=listener,
        public_origin=PUBLIC_ORIGIN,
        rp_id=REMOTE_RP_ID,
        tunnel_argv=(),
    )
    identity = EntranceIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor=ENTRANCE_ACTOR
    )
    return EntranceSettings(
        section=manifest.entrance,
        identity=identity,
        goal_spend_cap_usd=manifest.forage.spend_cap_per_goal_usd,
        plan=plan,
    )


async def _tables(hive: Hive) -> EntranceTables:
    """Open the Entrance's and the push channel's tables on the Hive's own database file."""
    db = hive.manifest.resolve_path(hive.manifest.hive.db)
    # Separate connections to one file, as `hive serve` opens them (ADR-0006); WAL makes it fine.
    store = await SqliteEntranceStore.create(connect(db), hive.clock)
    push = await SqliteSubscriptionStore.create(connect(db), hive.clock)
    return EntranceTables(store=store, push=push, trail=hive.stores.trail)


async def _keys(hive: Hive) -> EntranceKeys:
    """Load (or mint) the Hive's key and the Web Push keys from the Hive's own secret store."""
    manifest = hive.manifest
    store = FileSecretStore(manifest.resolve_path(manifest.hive.secrets_dir))
    vapid = VapidSigner(await load_or_mint_vapid_key(store), VAPID_SUBJECT, hive.clock)
    # No certificate authority: the plan's remote listener speaks plain HTTP.
    return EntranceKeys(
        hive_signer=await load_or_mint_hive_signer(store),
        vapid=vapid,
        topic_key=await load_or_mint_topic_key(store),
    )


async def _own_addresses(served: ServedHive) -> frozenset[IPAddress]:
    """Every address this host answers on: no push destination may be one of them."""
    snapshot = await served.interfaces.snapshot()
    return frozenset(address for interface in snapshot for address in interface.addresses)


def _entrance_hive(hive: Hive) -> EntranceHive:
    """Hand the Entrance the Queen's door, the stores it reads and the Hive's one enforcer."""
    stores, deps = hive.stores, hive.queen_deps
    # Providers by name and kind only, as `hive serve` hands them over.
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
        census=hive.queen,
        telemetry=hive.telemetry,
        llm=llm,
        virtual_cells=virtual.lifecycle if virtual is not None else None,
    )
    return EntranceHive(
        queen=hive.queen, reads=reads, enforcer=hive.enforcer, policy=hive.enforcer.policy
    )


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
