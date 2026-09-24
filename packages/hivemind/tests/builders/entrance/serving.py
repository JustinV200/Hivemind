"""Serve a Hive Entrance in-process over a real Queen: uvicorn on loopback, push to a fake service.

``serving`` builds what ``hive serve`` builds, over in-memory tables: a real Queen (not ticking) on
``make_queen_deps``'s fakes, the Entrance's tables on her trail, the Hive's keys, and
``build_entrance`` over a loopback socket on a port the system chooses; with ``remote`` it also
serves a remote listener on another loopback port, plain HTTP (a test-only plan: every real remote
mode speaks TLS), so a test can reach both applications. Every push delivery (webhook POSTs and Web
Push requests alike) goes to one recording ``httpx.MockTransport``, the destination names resolving
from a static table. The console device is recorded APPROVED and loopback-bound, its password
hashed cheaply, so a test logs in with the real flow in milliseconds.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance that drive the Landing Board over real sockets.

Key invariants:
    - The Entrance runs in the rig's own task group and is stopped when the block exits.
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from builders.entrance.auth import PASSWORD, cheap_password_hash
from builders.entrance.landing import DeviceKey, LandingClient, LandingSession
from builders.entrance.records import ed25519_public_key, entry_event, make_device
from builders.queen import WardenEnd, make_queen_deps
from unit.entrance.push.support import OWN_ADDRESS, Recorder, StaticResolver

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.auth import SoftPasskey
from hivemind.entrance.auth.session import SOCKET_HELLO_DEADLINE_S
from hivemind.entrance.enrol import CONSOLE_CAPABILITIES, DeviceStatus, EntranceIdentity
from hivemind.entrance.expose import ExposurePlan, ListenerPlan
from hivemind.entrance.gate import HiveReads, LlmReads
from hivemind.entrance.notify import HumanChannelRelay
from hivemind.entrance.push import (
    MemorySubscriptionStore,
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
    bind_listener,
    build_entrance,
)
from hivemind.entrance.store import MemoryEntranceStore
from hivemind.entrance.streams import DEFAULT_BACKLOG, TelemetryBoard
from hivemind.llm import FakeLLMProvider
from hivemind.manifest import EntranceExposure, EntranceSection
from hivemind.queen import Queen
from hivemind.queen.deps import QueenDeps
from waggle.clock import SystemClock
from waggle.signing import Ed25519Signer

LOOPBACK_HOST = "127.0.0.1"  # Where both test listeners bind.
PUBLIC_ORIGIN = "https://hive.example.ts.net"  # The remote listener's public_url in a remote rig.
REMOTE_RP_ID = "hive.example.ts.net"  # Its relying party.
VAPID_SUBJECT = "mailto:ops@example.net"  # The Web Push contact every rig signs with.
GOAL_SPEND_CAP_USD = 2.0  # [forage] spend_cap_per_goal_usd for every rig.
# Generous limits: a test makes many requests from one address, and limits have their own tests.
RIG_SECTION = EntranceSection(rate_limit_per_device=10_000, rate_limit_per_address=10_000)

__all__ = [
    "GOAL_SPEND_CAP_USD",
    "PUBLIC_ORIGIN",
    "RIG_SECTION",
    "VAPID_SUBJECT",
    "ProgramGrant",
    "RigOptions",
    "ServingRig",
    "serving",
]


@dataclass(frozen=True, slots=True)
class RigOptions:
    """How a rig differs from the default loopback-only Entrance.

    Attributes:
        remote: Also serve a remote listener (plain HTTP, test only).
        section: The ``[entrance]`` section the Entrance reads its thresholds from.
        push_statuses: What the fake push service answers, in order (then 201).
        provider: The Queen's scripted model provider; a silent one when omitted.
        remote_host: Where the remote listener binds (an address this host lacks fails).
        hello_deadline_s: How long a socket may take to send its first frame.
        stream_backlog: How far a live view may fall behind before it is closed.
    """

    remote: bool = False
    section: EntranceSection = RIG_SECTION
    push_statuses: tuple[int, ...] = ()
    provider: FakeLLMProvider | None = None
    remote_host: str = LOOPBACK_HOST
    hello_deadline_s: float = SOCKET_HELLO_DEADLINE_S
    stream_backlog: int = DEFAULT_BACKLOG


@dataclass(frozen=True, slots=True)
class ProgramGrant:
    """What an approval grants a program, and which listener it logs in on.

    Attributes:
        capabilities: What it may do.
        spend_cap_usd_per_day: Its daily spend cap.
        interactive: Whether a person types the password at it (it may confirm and step up).
        remote: Enrol and log in on the remote listener (a remote rig only).
    """

    capabilities: tuple[str, ...] = ("observe", "entrance:submit", "entrance:answer")
    spend_cap_usd_per_day: float = 5.0
    interactive: bool = False
    remote: bool = False


@dataclass(frozen=True, slots=True)
class ServingRig:
    """A running Entrance over a real Queen, and handles on everything a test inspects."""

    entrance: HiveEntrance
    queen: Queen
    deps: QueenDeps
    warden_end: WardenEnd
    telemetry: TelemetryBoard
    store: MemoryEntranceStore
    push_service: Recorder
    console: DeviceKey
    clock: SystemClock
    http: list[httpx.AsyncClient] = field(default_factory=list)

    @property
    def hive_id(self) -> str:
        """The Hive every signed string names."""
        return self.deps.identity.hive_id

    @property
    def loopback_url(self) -> str:
        """The loopback listener's base URL."""
        return f"http://{LOOPBACK_HOST}:{self.entrance.listeners.loopback_port}"

    @property
    def remote_url(self) -> str:
        """The remote listener's base URL (a remote rig only)."""
        port = self.entrance.listeners.remote_port
        assert port is not None, "this rig serves no remote listener"
        return f"http://{LOOPBACK_HOST}:{port}"

    def client(self, remote: bool = False) -> LandingClient:
        """Return a device client for one listener; the rig closes it on exit."""
        base = self.remote_url if remote else self.loopback_url
        http = httpx.AsyncClient(base_url=base, timeout=5.0)
        self.http.append(http)
        return LandingClient(http, self.hive_id, self.clock)

    async def until(self, check: Callable[[], bool], timeout_s: float = 3.0) -> None:
        """Wait until ``check`` holds (a socket admitted, a view subscribed), or fail.

        Args:
            check: Reads the running Entrance's own state.
            timeout_s: The longest to wait.
        """
        async with asyncio.timeout(timeout_s):
            while True:
                if check():
                    return
                await asyncio.sleep(0.005)

    async def console_session(self) -> tuple[LandingClient, LandingSession]:
        """Log the console in on loopback, as the operator at the Hive Stand."""
        client = self.client()
        return client, await client.login(self.console, PASSWORD)

    @property
    def loopback_origin(self) -> str:
        """The loopback listener's origin by name: the one a browser's page (and passkey) has."""
        return f"http://localhost:{self.entrance.listeners.loopback_port}"

    async def browser(
        self, grant: ProgramGrant | None = None
    ) -> tuple[LandingClient, LandingSession]:
        """Enrol a browser with a passkey on loopback, approve it as the console, log it in.

        Args:
            grant: What the approval grants; the defaults when omitted.

        Returns:
            The browser's client and its session, bound to a WebCrypto P-256 key.
        """
        active = grant if grant is not None else ProgramGrant(interactive=True)
        console, session = await self.console_session()
        invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "phone"})
        assert invite.status_code == 201, invite.text
        client, passkey = self.client(), SoftPasskey(self.loopback_origin)
        device_id = await client.enrol_browser(invite.json()["code"], passkey)
        body = {
            "name": "phone",
            "capabilities": list(active.capabilities),
            "spend_cap_usd_per_day": active.spend_cap_usd_per_day,
        }
        path = f"/v1/entrance/pending/{device_id}/approve"
        approved = await console.call(session, "POST", path, body)
        assert approved.status_code == 200, approved.text
        return client, await client.login_browser(device_id, passkey)

    async def program(
        self, grant: ProgramGrant | None = None
    ) -> tuple[LandingClient, LandingSession]:
        """Enrol a program over HTTP, approve it on loopback as the console, and log it in.

        Args:
            grant: What the approval grants and where the program logs in; the defaults when
                omitted.

        Returns:
            The program's client (on its listener) and its open session.
        """
        active = grant if grant is not None else ProgramGrant()
        console, session = await self.console_session()
        invite = await console.call(session, "POST", "/v1/entrance/invites", {"label": "bot"})
        assert invite.status_code == 201, invite.text
        client = self.client(remote=active.remote)
        key = await client.enrol(invite.json()["code"])
        body = {
            "name": "garden-bot",
            "capabilities": list(active.capabilities),
            "spend_cap_usd_per_day": active.spend_cap_usd_per_day,
            "interactive": active.interactive,
        }
        approved = await console.call(
            session, "POST", f"/v1/entrance/pending/{key.device_id}/approve", body
        )
        assert approved.status_code == 200, approved.text
        return client, await client.login(key)


@asynccontextmanager
async def serving(options: RigOptions | None = None) -> AsyncIterator[ServingRig]:
    """Run an Entrance over a real Queen until the block exits.

    Args:
        options: How the rig differs from the default; loopback only when omitted.

    Yields:
        The rig, both listeners serving.
    """
    active = options if options is not None else RigOptions()
    clock = SystemClock()
    relay, telemetry = HumanChannelRelay(), TelemetryBoard()
    deps, link, warden_end = make_queen_deps(
        clock, fake_provider=active.provider, human_channel=relay, on_heartbeat=telemetry.record
    )
    queen = Queen(deps)
    await queen.attach_warden(link)
    store, push_store = MemoryEntranceStore(deps.trail), MemorySubscriptionStore()
    console = await _console(store, clock)
    recorder, resolver = Recorder(*active.push_statuses), StaticResolver()
    async with recorder.client() as push_http:
        parts = EntranceParts(
            settings=_settings(deps, active),
            tables=EntranceTables(store=store, push=push_store, trail=deps.trail),
            hive=_hive(queen, deps, telemetry),
            keys=await _keys(clock),
            clock=clock,
            http=push_http,
            own_addresses=frozenset({ipaddress.ip_address(OWN_ADDRESS)}),
            resolver=resolver,
        )
        built = build_entrance(parts, bind_listener(LOOPBACK_HOST, 0))
        relay.bind(built.human_channel)
        rig = ServingRig(
            entrance=built.entrance,
            queen=queen,
            deps=deps,
            warden_end=warden_end,
            telemetry=telemetry,
            store=store,
            push_service=recorder,
            console=console,
            clock=clock,
        )
        async with _running(rig):
            yield rig


def _hive(queen: Queen, deps: QueenDeps, telemetry: TelemetryBoard) -> EntranceHive:
    """The Hive as the Entrance sees it: the Queen's door, her stores and tables, her enforcer."""
    # Every provider the bindings name is the rig's fake one.
    llm = LlmReads(
        providers={binding.provider: "fake" for binding in deps.bindings},
        bindings=deps.bindings,
        cluster=deps.cluster_state,
        health=deps.health_poller,
    )
    reads = HiveReads(
        goal_requests=deps.goal_requests,
        chat=deps.chat,
        chamber=deps.chamber,
        trail=deps.trail,
        memory=deps.memory,
        ledger=deps.ledger,
        census=queen,
        telemetry=telemetry,
        llm=llm,
    )
    return EntranceHive(
        queen=queen, reads=reads, enforcer=deps.enforcer, policy=deps.enforcer.policy
    )


@asynccontextmanager
async def _running(rig: ServingRig) -> AsyncIterator[None]:
    """Run the rig's Entrance in a task group; close the rig's clients and stop it on exit."""
    async with asyncio.TaskGroup() as group:
        group.create_task(rig.entrance.run())
        try:
            await rig.entrance.wait_running()
            yield
        finally:
            for http in rig.http:
                await http.aclose()
            await rig.entrance.stop()


def _settings(deps: QueenDeps, options: RigOptions) -> EntranceSettings:
    """What the manifest would decide: the section, and a loopback or test-remote plan."""
    loopback = ListenerPlan(LOOPBACK_HOST, 0, None)
    plan = ExposurePlan(
        mode=EntranceExposure.LOOPBACK,
        loopback=loopback,
        remote=None,
        public_origin=None,
        rp_id=None,
        tunnel_argv=(),
    )
    if options.remote:
        plan = ExposurePlan(
            mode=EntranceExposure.VPN,
            loopback=loopback,
            remote=ListenerPlan(options.remote_host, 0, None),
            public_origin=PUBLIC_ORIGIN,
            rp_id=REMOTE_RP_ID,
            tunnel_argv=(),
        )
    identity = EntranceIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
    )
    return EntranceSettings(
        section=options.section,
        identity=identity,
        goal_spend_cap_usd=GOAL_SPEND_CAP_USD,
        plan=plan,
        poll_interval_s=0.02,
        hello_deadline_s=options.hello_deadline_s,
        stream_backlog=options.stream_backlog,
    )


async def _keys(clock: SystemClock) -> EntranceKeys:
    """Mint the Hive's key and the Web Push keys in a throwaway secret store."""
    secrets = MemorySecretStore()
    vapid = VapidSigner(await load_or_mint_vapid_key(secrets), VAPID_SUBJECT, clock)
    return EntranceKeys(
        hive_signer=Ed25519Signer.generate(),
        vapid=vapid,
        topic_key=await load_or_mint_topic_key(secrets),
    )


async def _console(store: MemoryEntranceStore, clock: SystemClock) -> DeviceKey:
    """Record the operator's password (cheaply hashed) and the loopback-bound console device."""
    await store.set_operator_password_hash(cheap_password_hash(PASSWORD), clock.now())
    signer = Ed25519Signer.generate()
    console = make_device(
        clock,
        DeviceStatus.APPROVED,
        name="console",
        loopback_bound=True,
        interactive=True,
        expires_at=None,
        public_key=ed25519_public_key(signer),
        capabilities=CONSOLE_CAPABILITIES,
        spend_cap_usd_per_day=None,
    )
    await store.put_device(console, entry_event(console, clock))
    return DeviceKey(console.id, signer)
