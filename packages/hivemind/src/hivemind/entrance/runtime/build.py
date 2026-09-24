"""Wire the Hive Entrance from its parts: sessions, push, enrolment, both applications, the runtime.

``build_entrance`` is the Entrance's own composition (codingrules 13): given the typed parts the
``hive serve`` composition root read from the manifest, and the loopback socket it already bound,
it builds every collaborator once and returns the ``HiveEntrance`` ready to run, plus the Queen's
``HumanChannel`` over push. Each listener gets its own WebAuthn relying party (``localhost`` on
loopback, the exposure plan's name remotely) and its own login and enrolment challenges; the
tables, the session book, the push stack, the limits and the seams are shared. The loopback port
the socket really got shapes the loopback origin, relying party and Host check, which is why the
socket is bound first and the applications are built here, afterwards.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Called by the
    ``hive serve`` composition root and by tests. Calls into every Entrance package.

Key invariants:
    - One table builds both applications; the remote application exists only when exposed.
    - Every collaborator is built once; nothing here performs I/O.

See Also:
    - hivemind.entrance.runtime.parts for what is passed in.
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the process shape.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass
from datetime import timedelta

from starlette.types import ASGIApp

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.app import ListenerOptions, build_listener_app, route_table
from hivemind.entrance.auth import ChallengeBook, PasswordHasher, RelyingParty
from hivemind.entrance.auth.limits import DenialCounter, RateLimiter
from hivemind.entrance.auth.login import (
    LOGIN_CHALLENGE_TTL,
    AuthDeps,
    LoginCeremony,
    LoginGuards,
)
from hivemind.entrance.auth.session import Listener, SessionBook, SessionRules
from hivemind.entrance.auth.travel import open_travel_lock
from hivemind.entrance.enrol import (
    ENROLMENT_CHALLENGE_TTL,
    EnrolmentCeremony,
    EnrolmentDeps,
    EnrolmentRecords,
    EnrolmentRules,
    EnrolmentSeams,
)
from hivemind.entrance.gate import (
    EntranceRules,
    EntranceServices,
    GateGuards,
    ListenerDeps,
    PushServices,
    StreamServices,
    Switch,
)
from hivemind.entrance.landing_board import render_document
from hivemind.entrance.notify import PushHumanChannel, PushOutbox, PushSecurityNotifier
from hivemind.entrance.push import (
    Admission,
    ChannelKind,
    DestinationGuard,
    DestinationPolicy,
    LivePush,
    PushChannel,
    PushChannels,
    PushDispatcher,
    WebhookPush,
    WebPush,
)
from hivemind.entrance.reducer import EntranceReducer, ReducerSeams
from hivemind.entrance.runtime.entrance import EntranceWorkers, HiveEntrance
from hivemind.entrance.runtime.listeners import EntranceListeners, RemoteSetup, TunnelLaunch
from hivemind.entrance.runtime.parts import EntranceParts
from hivemind.entrance.runtime.seams import EntranceOffboarder, QueenGoalLedger
from hivemind.entrance.runtime.tls import RemoteTls
from hivemind.entrance.store import MemorySessionTable, SplitSessionTable
from hivemind.entrance.streams import ReduceOrderFollower, SocketRegistry, StreamHub

RELYING_PARTY_NAME = "HiveMind"  # What an authenticator shows the person for either listener.
LOOPBACK_RP_ID = "localhost"  # The loopback listener's relying party: WebAuthn refuses an address.

__all__ = ["LOOPBACK_RP_ID", "RELYING_PARTY_NAME", "BuiltEntrance", "build_entrance"]


@dataclass(frozen=True, slots=True)
class BuiltEntrance:
    """What ``build_entrance`` returns.

    Attributes:
        entrance: The Hive Entrance, ready to run.
        human_channel: The Queen's ``HumanChannel`` over push (bind the Queen's relay to it).
    """

    entrance: HiveEntrance
    human_channel: PushHumanChannel


@dataclass(frozen=True, slots=True)
class _Push:
    """The push stack every listener shares."""

    dispatcher: PushDispatcher
    live: LivePush
    outbox: PushOutbox


@dataclass(frozen=True, slots=True)
class _Shared:
    """What every listener's dependencies share, built once."""

    records: EnrolmentRecords
    book: SessionBook
    seams: EnrolmentSeams
    limiter: RateLimiter
    hasher: PasswordHasher


def build_entrance(parts: EntranceParts, loopback: socket.socket) -> BuiltEntrance:
    """Build the Hive Entrance over ``parts``, its loopback listener on ``loopback``.

    Args:
        parts: The tables, the Hive, the keys and what the manifest decides.
        loopback: The loopback listener's socket, already bound (port 0 resolved).

    Returns:
        The Entrance ready to run, and the Queen's channel over push.

    Raises:
        InvariantViolationError: The plan asks for TLS without the Hive's certificate authority.
        TravelLockUnavailableError: ``travel_lock`` is on where it cannot run.
    """
    port = int(loopback.getsockname()[1])
    push, sockets = _push(parts), SocketRegistry()
    shared = _shared(parts, push, sockets, port)
    listeners = EntranceListeners(loopback, _remote_setup(parts))
    seams = ReducerSeams(listeners, sockets, shared.seams.notifier)
    reducer = EntranceReducer(shared.records, shared.book, seams)
    hub = StreamHub(parts.tables.trail, parts.clock, parts.settings.poll_interval_s)
    served = _listener_deps(parts, shared, port)
    services = EntranceServices(
        queen=parts.hive.queen,
        hive=parts.hive.reads,
        listeners=served,
        push=_push_services(parts, push),
        guards=_guards(parts, shared, served[Listener.LOOPBACK].enrolment),
        reducer=reducer,
        streams=StreamServices(hub, sockets),
        rules=_rules(parts),
        door=listeners,
        clock=parts.clock,
    )
    listeners.mount(_apps(parts, services, port))
    follower = ReduceOrderFollower(hub, reducer, shared.records.trail)
    workers = EntranceWorkers(hub, push.outbox, follower)
    return BuiltEntrance(HiveEntrance(services, listeners, workers), PushHumanChannel(push.outbox))


def _shared(parts: EntranceParts, push: _Push, sockets: SocketRegistry, port: int) -> _Shared:
    """Build what both listeners share: the records, the session book, the seams, the limits."""
    settings, clock = parts.settings, parts.clock
    records = EnrolmentRecords(parts.tables.store, parts.tables.trail, clock, settings.identity)
    rules = SessionRules.from_section(settings.section, _origins(parts, port))
    # The console's sessions stay in memory; every other device's are durable.
    tables = SplitSessionTable(parts.tables.store.sessions, MemorySessionTable())
    book = SessionBook(records, rules, tables)
    seams = EnrolmentSeams(
        notifier=PushSecurityNotifier(push.outbox),
        offboarder=EntranceOffboarder(book, push.dispatcher, sockets),
        goals=QueenGoalLedger(parts.hive.reads.goal_requests, parts.hive.queen),
    )
    section = settings.section
    limiter = RateLimiter(clock, section.rate_limit_per_device, section.rate_limit_per_address)
    return _Shared(records, book, seams, limiter, PasswordHasher())


def _rules(parts: EntranceParts) -> EntranceRules:
    """The thresholds routes decide with, from the manifest."""
    section = parts.settings.section
    return EntranceRules(
        step_up_spend=section.step_up_spend,
        goal_spend_cap_usd=parts.settings.goal_spend_cap_usd,
        steward_devices=section.steward_devices,
    )


def _origins(parts: EntranceParts, port: int) -> dict[Listener, frozenset[str]]:
    """Return each listener's own origin: the only one a browser's socket may open from."""
    origins = {Listener.LOOPBACK: frozenset({_loopback_origin(port)})}
    public = parts.settings.plan.public_origin
    if public is not None:
        origins[Listener.REMOTE] = frozenset({public})
    return origins


def _loopback_origin(port: int) -> str:
    """Return the loopback listener's origin, by name: the loopback relying party needs one."""
    return f"http://{LOOPBACK_RP_ID}:{port}"


def _relying_parties(parts: EntranceParts, port: int) -> dict[Listener, RelyingParty]:
    """Return each served listener's relying party: localhost, and the plan's name remotely."""
    parties = {
        Listener.LOOPBACK: RelyingParty(
            id=LOOPBACK_RP_ID, name=RELYING_PARTY_NAME, origins=(_loopback_origin(port),)
        )
    }
    plan = parts.settings.plan
    if plan.remote is not None and plan.rp_id is not None and plan.public_origin is not None:
        parties[Listener.REMOTE] = RelyingParty(
            id=plan.rp_id, name=RELYING_PARTY_NAME, origins=(plan.public_origin,)
        )
    return parties


def _listener_deps(
    parts: EntranceParts, shared: _Shared, port: int
) -> dict[Listener, ListenerDeps]:
    """Build each served listener's login and enrolment dependencies over its relying party."""
    section, clock = parts.settings.section, parts.clock
    # An invite link names where the device will redeem it: public_url when exposed.
    rules = EnrolmentRules(
        policy=parts.hive.policy,
        invite_ttl=timedelta(minutes=section.invite_ttl_minutes),
        pending_ttl=timedelta(hours=section.pending_ttl_hours),
        invite_base_url=parts.settings.plan.public_origin or _loopback_origin(port),
    )
    public_key = parts.keys.hive_signer.public_key_bytes
    # Each listener's challenges are its own: a ceremony never crosses relying parties.
    enrolments = {
        listener: EnrolmentDeps(
            shared.records,
            rules,
            EnrolmentCeremony(public_key, party, ChallengeBook(clock, ENROLMENT_CHALLENGE_TTL)),
            shared.seams,
        )
        for listener, party in _relying_parties(parts, port).items()
    }
    # The lock records and notifies through the records and seams both listeners share.
    travel = open_travel_lock(section, enrolments[Listener.LOOPBACK])
    guards = LoginGuards(shared.limiter, section.lockout_attempts, travel)
    deps: dict[Listener, ListenerDeps] = {}
    for listener, enrolment in enrolments.items():
        party = enrolment.ceremony.relying_party
        login = LoginCeremony(ChallengeBook(clock, LOGIN_CHALLENGE_TTL), (party,), shared.hasher)
        deps[listener] = ListenerDeps(
            listener, AuthDeps(enrolment, shared.book, login, guards), enrolment
        )
    return deps


def _guards(parts: EntranceParts, shared: _Shared, enrolment: EnrolmentDeps) -> GateGuards:
    """Build the enforcer, the denial-burst counter and the limiter every request passes."""
    section = parts.settings.section
    # The counter locks a device through enrolment's flow, over the shared records and seams.
    window = timedelta(seconds=section.lockout_denial_window_s)
    denials = DenialCounter(enrolment, section.lockout_denials, window)
    return GateGuards(parts.hive.enforcer, denials, shared.limiter)


def _push(parts: EntranceParts) -> _Push:
    """Build the push stack: the destination guard, the channels offered, the dispatcher."""
    section, keys = parts.settings.section, parts.keys
    guard = DestinationGuard(
        DestinationPolicy.from_manifest(section, parts.own_addresses), parts.resolver
    )
    stored: dict[ChannelKind, PushChannel] = {}
    if section.push.webhooks:
        stored[ChannelKind.WEBHOOK] = WebhookPush(parts.http, keys.hive_signer, guard, parts.clock)
    # Web Push is offered only with a VAPID signer: without its contact nothing can be signed.
    offered = section.push
    if section.push.web_push and keys.vapid is not None:
        stored[ChannelKind.WEB_PUSH] = WebPush(parts.http, keys.vapid, keys.topic_key, guard)
    elif section.push.web_push:
        offered = section.push.model_copy(update={"web_push": False})
    live = LivePush()
    dispatcher = PushDispatcher(
        PushChannels(live, stored), parts.tables.push, Admission(offered, guard), parts.clock
    )
    return _Push(dispatcher, live, PushOutbox(dispatcher, parts.tables.store, parts.clock))


def _push_services(parts: EntranceParts, push: _Push) -> PushServices:
    """Describe the push channel as routes see it."""
    vapid = parts.keys.vapid
    return PushServices(
        dispatcher=push.dispatcher,
        live=push.live,
        hive_public_key=parts.keys.hive_signer.public_key_bytes,
        vapid_public_key=vapid.application_server_key if vapid is not None else None,
    )


def _remote_setup(parts: EntranceParts) -> RemoteSetup | None:
    """Describe the remote listener's start, or None when the plan exposes nothing."""
    plan, keys = parts.settings.plan, parts.keys
    if plan.remote is None:
        return None
    tls = None
    if plan.remote.tls is not None:
        if keys.authority is None:
            raise InvariantViolationError("A TLS listener needs the Hive's certificate authority.")
        tls = RemoteTls(plan.remote.tls, keys.authority, keys.serials, parts.clock)
    tunnel = None
    if plan.tunnel_argv:
        env = parts.settings.tunnel_env if parts.settings.tunnel_env is not None else {}
        tunnel = TunnelLaunch(plan.tunnel_argv, env, parts.clock)
    return RemoteSetup(plan.remote, tls, tunnel)


def _apps(parts: EntranceParts, services: EntranceServices, port: int) -> dict[Listener, ASGIApp]:
    """Build each served listener's application from the one route table."""
    settings = parts.settings
    table, document = route_table(), render_document()
    switches = (
        frozenset({Switch.STEWARD_DEVICES}) if settings.section.steward_devices else frozenset()
    )
    apps = {
        Listener.LOOPBACK: build_listener_app(
            table,
            services,
            ListenerOptions(
                Listener.LOOPBACK, switches, document, bound_port=port, web_root=settings.web_root
            ),
        )
    }
    if Listener.REMOTE in services.listeners:
        options = ListenerOptions(
            Listener.REMOTE,
            switches,
            document,
            cors_origin=settings.plan.public_origin,
            web_root=settings.web_root,
        )
        apps[Listener.REMOTE] = build_listener_app(table, services, options)
    return apps
