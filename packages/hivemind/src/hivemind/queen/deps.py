"""Define WardenLink and QueenDeps: every collaborator, and every attached Warden's own wire.

`hivemind.queen.queen.Queen.__init__` takes a `WardenId` per attached link and one `QueenDeps`
bundle (codingrules section 5.1: "introduce a frozen dataclass for the argument group"), the same
role `hivemind.wardens.deps.WardenDeps` plays for a `Warden`. `QueenDeps` carries every collaborator
the Queen's tick touches: her task store (`chamber`), her hot-state and durable memory
(`memory`, `identity`, `clock`), her audit sink (`trail`), her escalation playbook
(`policy`, `alarm_attempt_limit`), how she resolves and rebinds a model slot without ever holding
a `HiveManifest` (`bound_for`, `rebind`, `bindings`, `call_gate`, `map`), her share of Forage
(`budgets`), her live book of it (`ledger`, roadmap step 4.7), her liveness cadence
(`heartbeat_interval_s`, `heartbeat_miss_limit`), the slice of `[memory]` an awake episode's
prompt is budgeted against (`memory_budget`), the Hive Stand's own resolved scratch root
(`scratch_root`, roadmap step 5.0b), read only by `hivemind.queen.goal_submission.submit_goal` so
a declared `PlannedLeaving` inside it is caught while planning, not discovered at release, and,
roadmap step 5.7 (ADR-0028), her placement inputs: `placement_policy`, the Virtual side's own
inventory (`virtual_backends`, `dormant_cells`), and the seam that turns a Virtual `Placement`
into a `WardenLink` (`virtual_provider`, a `VirtualCellProvider`, defined here beside `WardenLink`
rather than in `hivemind.queen.placement` since it names `WardenLink`/`Task` and that package must
never import this one back). `WardenLink` is the Queen-side half of one attached Warden's own
Waggle link: `hivemind.wardens.deps.WardenDeps.queen_link`/`.hop` is the Warden's own end of the
exact same pair. Roadmap step 10.5 (ADR-0040) adds her human end: the durable goal-request table
(`goal_requests`), the chat log (`chat`), the seam that tells the human's devices something is
waiting (`human_channel`), and two small pieces of her own runtime bookkeeping kept here beside
`housekeeping`: the in-process `wake` signal her tick awaits beside her Warden links, and the
`planning` lane her one in-flight goal plan runs in (`PlanningLane`). Roadmap step 10.6b adds the
untrusted-content scanner a human's chat message passes through before her episode reads it
(`scanner`). The Hive Entrance's read side adds two more: `on_heartbeat`, the hook every Heartbeat
she receives is handed to (the composition root wires it to the Entrance's telemetry stream, since
a Heartbeat never reaches the trail), and `intake_lock`, which serialises every goal-request edge so
a revocation from the Entrance and her own intake never move one request from a stale value.
Roadmap step 10.6 adds the Guard Bee (the Hive's security watcher) she runs on her own tick beside
the House Bee's sweep (`guard_bee`): the composition root builds one for every Hive it composes
(`hivemind.cli.compose.guard`), and a test that needs none leaves it None.
The zero-grant fix adds her book of fresh tasks waiting for a grant (a `GrantWaits`, kept in
`hivemind.queen.holds`, holding `[forage] zero_grant_patience_s`), and to `WardenLink` the reader of
its Cell's capacity as it stands right now (`live_capacity`, a `LiveCapacity`: the Hive Stand
re-reads its load and free memory on every call; a Virtual Cell, whose resources its spec fixes, has
none). The dispatcher lifecycle fix adds her Virtual Cells being acquired beside her tick
(`ProvisionLane`, a bounded set of `ProvisionJob`s, so a slow provision never stalls her loop), and
gathers the dispatcher's own runtime bookkeeping -- its lock, those waits and that lane -- into one
`DispatchBook` (`dispatch`), kept here beside `housekeeping` for the same reason and so `QueenDeps`
stays inside codingrules 5.1's class size. The backend backoff adds to that book the Virtual
backends whose provisions keep failing, each held back for a while (`ProvisionBackoff`, one
`BackendHold` per backend), its floor and cap her own defaults rather than manifest settings. The
release fix adds her Virtual Cells being released beside her tick (`ReleaseLane`), bounded the same
way.
Roadmap step 10.6a adds `guard`: her durable Guard requests, the dire patterns she
decides by rule, and what isolating a Cell needs (`hivemind.queen.guard_requests.GuardDeps`).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Built once per Queen by whichever
    composition root constructs one -- the CLI (roadmap step 3.21) in production,
    `tests.builders.queen.make_queen_deps` in tests -- which attaches each `WardenLink` with
    `Queen.attach_warden` before `run()`. Calls into `hivemind.brood_chamber`, `hivemind.forage`,
    `hivemind.guard` (Enforcer, roadmap step 10.3), `hivemind.llm.ladders.gate`,
    `hivemind.llm.slots`, `hivemind.memory`, `hivemind.pheromone`, `hivemind.queen.chat`
    (ChatLog, HumanChannel, NullHumanChannel -- roadmap step 10.5), `hivemind.queen.intake`
    (GoalRequestStore -- roadmap step 10.5), `hivemind.queen.guard_requests` (GuardDeps --
    roadmap step 10.6a), `hivemind.queen.cluster.health`/`.orders`
    (HealthPoller, OrderStore, InMemoryOrderStore -- roadmap step 4.9),
    `hivemind.queen.forage.ledger` (ForageLedger), `hivemind.queen.placement`
    (PlacementPolicy, VirtualBackendCandidate, DormantCandidate, Placement -- roadmap step 5.7),
    `hivemind.supervision`, `hivemind.workers.roles.guard_bee` (GuardBee -- roadmap step 10.6)
    and waggle only.

Key invariants:
    - `QueenDeps` and `WardenLink` are frozen and slotted (codingrules section 8.5): neither is
      itself read from or written to JSON/TOML, so both are dataclasses, not pydantic BaseModels,
      matching `WardenDeps`.
    - `QueenDeps` carries manifest *slices* only, never a `HiveManifest` itself (codingrules
      section 13): the composition root converts.
    - `bound_for` is scoped to whichever `ModelSlot` a caller asks for (`QUEEN`, `ATTENDANT`, or a
      task's own slot for a fresh binding); `rebind` walks the same `bindings` chain from a named
      key instead of a slot's own manifest key, for a REBIND within the fallback chain.
    - `WardenLink.send` is the only way Queen-side code sends on a Warden's own link (phase-7
      handoff open item 8): it never lets `TransportClosedError`/`ConnectionLostError` escape, so
      a Warden whose link has already gone can never crash the Queen's tick loop
      (`waggle.loop.TickLoop._recoverable_errors` names neither); it reports whether the frame
      actually went out, so a caller with durable state at stake can react.

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit this bundle exists to keep.
    - .claude/codingrules.md section 13 for "the Queen takes manifest slices, never a HiveManifest".
    - .claude/phase-7-handoff.md section 8 open item 8 for the guarded-send fix `WardenLink.send`
      implements.
    - docs/adr/0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md for why the Queen
      holds no session and no registry.
    - hivemind.wardens.deps for WardenDeps, the bundle this one is modelled on, and its own
      `send_guarded`, the identically-shaped guard for a Warden's own links.
    - hivemind.queen.queen for Queen, this bundle's one consumer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from hivemind.basket import Offer, TransferReceiver, TransferSender
from hivemind.brood_chamber import BroodChamber, Task, TaskOutcome
from hivemind.cell import Cell
from hivemind.common.logging import get_logger
from hivemind.forage import (
    ForageCapacity,
    ForageMap,
    GoalBudgets,
    ModelSlot,
    RoleFootprint,
    RoyalReserve,
    SlotBinding,
)
from hivemind.guard import Enforcer
from hivemind.guard.scanner import ContentScanner, default_content_scanner
from hivemind.hive import VirtualCellSpec
from hivemind.honey_store.access import HoneyAccess
from hivemind.llm import BoundModel, CallGate, ProviderLookup
from hivemind.llm.routing import RoutingRules
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.queen.chat import ChatLog, HumanChannel, NullHumanChannel
from hivemind.queen.cluster.health import HealthPoller
from hivemind.queen.cluster.orders import InMemoryOrderStore, OrderStore
from hivemind.queen.forage.ledger import ForageLedger
from hivemind.queen.guard_requests import GuardDeps
from hivemind.queen.holds import GrantWaits, HeldCells, ResumeHints
from hivemind.queen.intake import GoalRequestStore
from hivemind.queen.peripherals import WatchBook
from hivemind.queen.placement import (
    DormantCandidate,
    Placement,
    PlacementPolicy,
    VirtualBackendCandidate,
)
from hivemind.queen.requeening.book import RequeeningBook
from hivemind.queen.state import ClusterState
from hivemind.queen.supersedure.ports import SupersedureBook
from hivemind.royal_jelly import QuarantineReport, RoyalJellyAccess, ToolSpec
from hivemind.supervision import EscalationPolicy
from hivemind.workers.roles.guard_bee import GuardBee
from waggle.clock import Clock
from waggle.envelope import Envelope, Hop
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.ids import CellId, GrantId, MessageId, NodeId, TaskId, ToolId, WardenId
from waggle.messages.supervision import Heartbeat
from waggle.messages.task import WorkerRole
from waggle.messages.tool import ToolQuarantineReport
from waggle.transport.base import Transport

# roadmap step 3.21 (second half): QueenDeps carried no [forage.roles.*]/[forage.reserve]/
# grant_ttl_s slice, so hivemind.queen.dispatcher stood in with these three module constants
# (flagged in that dispatch's own report); they move here, unchanged, as this dataclass's own
# defaults, so a caller that builds a QueenDeps without naming these three fields (every existing
# test) gets exactly today's behaviour, and hivemind.cli.compose.build_hive is the first caller
# that overrides them from a loaded HiveManifest's own [forage] section.
_DEFAULT_DRONE_FOOTPRINT = RoleFootprint(
    cpu_cores=1.0,
    memory_bytes=512 * 1024 * 1024,
    seats=1,
    token_rate_per_minute=1_000.0,
    exoskeleton_extra_memory_bytes=0,
)
_DEFAULT_GRANT_TTL_S = 300.0  # Matches the manifest's own [forage] grant_ttl_s default.
_DEFAULT_NURSE_MAX_ROUNDS = 2  # Matches the manifest's own [nurse] max_rounds default.
# roadmap step 4.6: MemoryBudget's own two threshold defaults, additive (module docstring on the
# class itself explains why they are not yet read from a manifest slice). 0.5 leaves real room
# before handoff_threshold's own two-thirds mark, so a bee that only needs compacting is caught
# before it is forced to hand off entirely.
_DEFAULT_COMPACT_AT = 0.5  # Matches manifest.schema.supervision.DEFAULT_COMPACT_AT.
_DEFAULT_HANDOFF_THRESHOLD = 0.66  # Matches manifest.schema.supervision.DEFAULT_HANDOFF_THRESHOLD.
# roadmap step 4.3's own wiring step: mirrors manifest.schema.supervision's own
# DEFAULT_SWEEP_INTERVAL_S/DEFAULT_HOT_WINDOW_S so a QueenDeps built without naming either field
# (every pre-housekeeping test) still runs a House Bee sweep on a sensible cadence.
_DEFAULT_SWEEP_INTERVAL_S = 3_600.0  # One hour.
_DEFAULT_HOT_WINDOW_S = 4.0 * 3600.0  # Four hours.
# Matches the manifest's own [forage] zero_grant_patience_s default, so a QueenDeps built without
# naming it (every test that never waits) bounds a wait exactly as a default manifest does.
# The most Virtual Cells acquired beside the tick at once: as many as a default manifest's
# [virtual_cells] max_cells lets exist, so the bound never starves a default Hive, while a Hive
# configured for many more Cells still starts no more than this many containers or VMs together.
_DEFAULT_PROVISIONS_IN_FLIGHT = 4
# The most Virtual Cells released beside the tick at once (a graceful stop of up to 5 s, then a
# teardown or a pause each): the same bound as provisioning, for the same reason, and a release
# past it waits its turn rather than being dropped.
_DEFAULT_RELEASES_IN_FLIGHT = 4
# How long placement rests a Virtual backend whose provisions keep failing: this long after one
# failed round, twice as long after each further one in a row, never longer than the cap. The
# Queen's own defaults, not manifest settings: a backend's outage is hers to wait out, and the
# cap means a backend that recovers is used again within five minutes of it.
_DEFAULT_BACKOFF_FLOOR_S = 1.0
_DEFAULT_BACKOFF_CAP_S = 300.0  # Five minutes.
# Phase-7 handoff open item 8: a transport this final (send after close) or this dead (a dropped
# link) is never this Queen's own bug to crash a tick over -- the caller reconnects or gives up,
# never this send. Exactly these two, nothing broader (codingrules section 10's "never `except
# Exception`" applies here too: a send helper that swallowed everything would hide a real bug).
_LINK_GONE = (TransportClosedError, ConnectionLostError)

log = get_logger(__name__)

__all__ = [
    "BackendHold",
    "DispatchBook",
    "DormantCellSource",
    "Housekeeping",
    "LiveCapacity",
    "MemoryBudget",
    "OnCellGranted",
    "OnHeartbeat",
    "OnTaskFinished",
    "PendingReport",
    "PlanningLane",
    "ProvisionBackoff",
    "ProvisionJob",
    "ProvisionLane",
    "QuarantineRecord",
    "QueenDeps",
    "ReleaseLane",
    "SandboxCellProvider",
    "ToolLane",
    "UplinkKeys",
    "VirtualBackendSource",
    "VirtualCellProvider",
    "WardenLink",
    "send_guarded",
]

# roadmap step 5.6/5.9's own live-feed seam (this dispatch's own reconciliation): QueenDeps.
# virtual_backends/dormant_cells started as plain static tuples (roadmap step 5.7); these three
# additive callables let hivemind.cli.compose.build_hive feed them from a live
# hivemind.hive.lifecycle.CellLifecycle instead, without hivemind.queen.dispatcher.snapshot ever
# importing hive.lifecycle itself (queen.dispatcher.snapshot converts the hive-layer candidate
# types into queen.placement.inventory ones; see that module's own docstring). None (every default)
# means "no live source": hivemind.queen.dispatcher.snapshot.build_inventory falls back to the
# static tuples exactly as it did before this dispatch, so every pre-5.6 test is unaffected.
VirtualBackendSource = Callable[[], Awaitable[tuple[VirtualBackendCandidate, ...]]]
DormantCellSource = Callable[[], Awaitable[tuple[DormantCandidate, ...]]]
# hivemind.queen.cell_gate.release.make_on_task_finished builds the one real implementation; see
# that module's own docstring for what it does with a finished task's own Cell.
OnTaskFinished = Callable[[CellId, TaskOutcome], Awaitable[None]]
# Told by hivemind.queen.dispatcher.ready the moment a task is granted onto a Cell, so a Virtual
# Cell's own READY -> GRANTED edge (hivemind.hive.cell_state) is driven by the dispatch that
# caused it, not synthesised later at release time. The composition root wires it to
# CellLifecycle.grant for Cells the lifecycle tracks, and to a no-op for every other Cell.
OnCellGranted = Callable[[CellId, GrantId], Awaitable[None]]
# Told about every Heartbeat the Queen receives (the Warden, the Heartbeat, when it arrived), after
# she has recorded it. Synchronous and cheap by contract: it runs inside her tick, so it may only
# hand the sample on (the Hive Entrance's telemetry board fans it out to its live views).
OnHeartbeat = Callable[[WardenId, Heartbeat, datetime], None]
# The zero-grant fix: a link's Cell's capacity as it stands right now, for a Cell whose capacity
# is refreshed live (the Hive Stand re-reads its load, free memory and free disk on every call).
# The dispatcher sizes every grant from it, and waits out only a shortfall in a figure it reads,
# since only such a figure can change while a task waits (a fact about the link, never a branch
# on the Cell's kind). The composition root sets it on the Hive Stand's own link.
LiveCapacity = Callable[[], Awaitable[ForageCapacity]]
# Phase 9 (ADR-0048): the raw Ed25519 public key a Warden's own node signs a Basket manifest with,
# looked up from the key the Cell gate holds for that link's Cell; None when the link proves none.
UplinkKeys = Callable[["WardenLink"], bytes | None]


async def send_guarded(transport: Transport, envelope: Envelope) -> bool:
    """Send `envelope` on `transport`; False, logged, when the link has already gone.

    Phase-7 handoff open item 8: every Queen -> Warden send used to call `transport.send`
    directly, so a Warden whose link had already closed or dropped raised
    `TransportClosedError`/`ConnectionLostError` straight out of the Queen's tick
    (`hivemind.queen.queen.Queen._recoverable_errors` names neither), ending `run()` for the
    whole Hive. `WardenLink.send` is the one method that calls this for a `WardenLink`; a caller
    that only holds a bare `Transport` (`hivemind.queen.cell_gate.listener`'s own reply sends,
    before any `WardenLink` exists) calls this directly instead.

    Args:
        transport: The link to send on; any `Transport`, including a `WardenLink.transport`.
        envelope: The already-wrapped frame to send.

    Returns:
        True once the frame was handed to the link; False when `transport.send` raised
        `TransportClosedError` or `ConnectionLostError`, logged as a warning naming the
        envelope's own ids and kind, never its payload.
    """
    try:
        # Queued at once on an in-process pair, or handed to the WebSocket's own send buffer;
        # never awaited on the far side, so this is never the slow half of a round trip.
        await transport.send(envelope)
    except _LINK_GONE:
        # The Warden's link is gone: logged so the gap is visible, never raised, so this can
        # never be the exception that ends the Queen's tick loop.
        log.warning(
            "queen.warden_link.send_failed",
            recipient=envelope.recipient,
            sender=envelope.sender,
            message_id=envelope.id,
            kind=envelope.kind,
        )
        return False
    return True


@dataclass(frozen=True, slots=True)
class WardenLink:
    """One attached Warden's own address, Cell and Waggle link, as the Queen sees it.

    Attributes:
        warden_id: The attached Warden's own id.
        cell: The Cell that Warden owns; placement (`hivemind.queen.placement.decide`) reads its
            capabilities and capacity, never the Warden's own session.
        transport: The Queen's own end of the `waggle.transport.memory.MemoryTransport` pair
            (or, in production, a WebSocket transport) to this Warden; the Warden holds the
            other end (`hivemind.wardens.deps.WardenDeps.queen_link`).
        hop: This Queen's own address (`sender`, `"hive_<id>"`), this Warden's address
            (`recipient`, `"warden_<id>"`) and the sending node id, stamped on every envelope the
            Queen wraps and sends to this Warden.
        live_capacity: Reads `cell`'s capacity as it stands right now, for a Cell whose capacity
            is refreshed live (the Hive Stand); None (the default) for one whose capacity is fixed
            for its life (a Virtual Cell's spec), whose grants are sized from `cell` itself.
        node_id: The node the Warden's frames are signed as, proved by the link (a Virtual
            Cell's handshake, the Hive Stand's own process); `warden.spawned` records it, so the
            Guard Bee knows which node speaks for which Cell (roadmap step 10.6). None when the
            link proves none (a test's in-memory pair).
        outlives_the_hive: The Warden runs on a colonized device and keeps working when the Hive
            Stand stops (ADR-0053); False (the default) for one that stops with the Hive (the
            Hive Stand's own, a Virtual Cell's), whose tasks a stop gives back.
    """

    warden_id: WardenId
    cell: Cell
    transport: Transport
    hop: Hop
    live_capacity: LiveCapacity | None = None
    node_id: NodeId | None = None
    outlives_the_hive: bool = False

    async def send(self, envelope: Envelope) -> bool:
        """Send `envelope` on this Warden's own link; see `send_guarded`.

        The one way any Queen-side code sends to this Warden (phase-7 handoff open item 8):
        every call site used to reach for `self.transport.send` directly, which could crash the
        Queen's tick loop the moment this link closed under it.

        Args:
            envelope: The already-wrapped frame to send.

        Returns:
            True once handed to the link; False, logged, when it had already closed or dropped.
        """
        return await send_guarded(self.transport, envelope)


class VirtualCellProvider(Protocol):
    """Acquire a WardenLink for a `ProvisionVirtual`/`ReuseDormant` Placement (roadmap step 5.6).

    `hivemind.queen.dispatcher` is this Protocol's one caller: when `hivemind.queen.placement.
    decide` returns a Virtual `Placement`, the dispatcher calls `acquire` to turn it into an
    attached `WardenLink`, the same shape an already-attached Real Cell's link already has.
    Defined here, beside `WardenLink` rather than in `hivemind.queen.placement` (a sibling
    package), because it names `WardenLink` and `Task` in its own signature and `queen.placement`
    must never import `queen.deps` back (that would cycle: `deps` already imports `PlacementPolicy`
    from `queen.placement`). Roadmap step 5.6 (Virtual Cell lifecycle) implements this over
    `hivemind.hive.CellBackend`; until then `QueenDeps.virtual_provider` stays `None` and a Virtual
    Placement raises `hivemind.queen.placement.PlacementError` instead of being acquired.
    """

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Provision or resume the Cell `placement` names, and return its own WardenLink.

        Args:
            placement: A `ProvisionVirtual` or `ReuseDormant` Placement `decide` returned.
            task: The task this Cell is being acquired for.

        Returns:
            A fresh `WardenLink`, attached the same way `Queen.attach_warden` attaches any other.

        Raises:
            hivemind.hive.CellProvisionError: The backend could not provision or resume the Cell;
                `hivemind.queen.dispatcher` re-enters placement once with that backend's own
                headroom zeroed before giving up (ADR-0028 Consequences).
        """
        ...


class SandboxCellProvider(Protocol):
    """Provision and release the disposable sandbox Cell a Quarantine Comb run happens in.

    Phase 9 (ADR-0047): a sandbox is the Queen's own, never handed to a requester, so it has no
    task and no `Placement`; `hivemind.queen.cell_gate.provider.LifecycleVirtualCellProvider`
    implements this beside `VirtualCellProvider` over the same lifecycle.
    """

    async def acquire_sandbox(self, spec: VirtualCellSpec) -> WardenLink:
        """Provision a Cell from `spec`, wait for its Warden, and return that Warden's link.

        Raises:
            hivemind.hive.CellProvisionError: The backend could not provision it or it never
                became reachable.
        """
        ...

    async def release_sandbox(self, cell_id: CellId) -> None:
        """Tear the sandbox down; never raises for a Cell already gone."""
        ...


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    """What the Queen kept from one Quarantine Comb run, so a Warden may later promote on it.

    Attributes:
        spec: The spec the package was proved against.
        report: The Comb's report (passed or not).
        package_sha256: The package's hash; the Basket holds its bytes.
        requested_by: The Warden whose request this run served.
        cell_id: The one Cell a CELL-scope tool is for; None at HIVE scope.
    """

    spec: ToolSpec
    report: QuarantineReport
    package_sha256: str
    requested_by: WardenId
    cell_id: CellId | None


@dataclass(frozen=True, slots=True)
class PendingReport:
    """A `tool.quarantine` order sent to a sandbox Warden, and the future its report resolves."""

    warden_id: WardenId  # Only this Warden's reply resolves it.
    future: asyncio.Future[ToolQuarantineReport]


@dataclass(slots=True)
class ToolLane:
    """The Queen's tool work beside her tick: promotion jobs, brokered runs and Basket transfers.

    A promotion flow scaffolds with a model, a Quarantine Comb run waits for a sandbox Warden and
    a Basket shipment waits for acks; each is a job here, never an await in her tick
    (codingrules 8.8). The tick only starts a job or resolves a future a job awaits. `Queen.stop`
    cancels the jobs and awaits them (`hivemind.queen.ticks.tools.stop_tools`). Owns its own
    mutable state in place (codingrules section 8.5), in memory only: a restarted Queen runs no
    job she did not start and keeps no record she did not write.

    Attributes:
        limit: The most tool jobs in flight at once.
        jobs: Every request or brokered run in flight, by tool id.
        records: The spec and report of every run this Queen made, by (tool id, version).
        pending_reports: Orders awaiting a sandbox Warden's report, by the order's envelope id.
        transfers: Every blob shipment out, by (receiving Warden, sha256).
        shipped: The future each shipment's job awaits, resolved True on completion.
        receipts: Every blob upload in, by (sending Warden, sha256).
        offers: The offer each upload in was accepted on, for its provenance at the end.
        draining: The queued shipments (`hive basket ship`) being shipped right now, by row id.
        sandbox_cells: The sandbox Cells in use, which an announcement skips.
        background: Fire-and-forget jobs (announcements, shipments from `hive basket ship`).
        last_drain_at: When the queued shipments were last looked at.
        closed: Set as the Queen stops: no job is started afterwards.
    """

    limit: int = _DEFAULT_PROVISIONS_IN_FLIGHT
    jobs: dict[ToolId, asyncio.Task[None]] = field(default_factory=dict)
    records: dict[tuple[ToolId, str], QuarantineRecord] = field(default_factory=dict)
    pending_reports: dict[MessageId, PendingReport] = field(default_factory=dict)
    transfers: dict[tuple[WardenId, str], TransferSender] = field(default_factory=dict)
    shipped: dict[tuple[WardenId, str], asyncio.Future[bool]] = field(default_factory=dict)
    receipts: dict[tuple[WardenId, str], TransferReceiver] = field(default_factory=dict)
    offers: dict[tuple[WardenId, str], Offer] = field(default_factory=dict)
    draining: set[int] = field(default_factory=set)
    sandbox_cells: set[CellId] = field(default_factory=set)
    background: set[asyncio.Task[None]] = field(default_factory=set)
    last_drain_at: datetime | None = None
    closed: bool = False


@dataclass(frozen=True, slots=True)
class MemoryBudget:
    """The slice of the manifest's `[memory]` section an awake episode's prompt is sized against.

    Attributes:
        budget_fraction: Fraction of a bound model's context window reserved for assembled
            content (`[memory] budget_fraction`).
        output_reserve_tokens: Tokens reserved for the model's own reply, never spent on
            assembled content (`[memory] output_reserve_tokens`).
        compact_at: Fraction of the context window past which `hivemind.queen.ticks.context.
            intervention_for` orders `COMPACT` (roadmap step 4.6). Additive: `QueenDeps` carries
            no manifest slice naming this field yet (`hivemind.manifest.schema.supervision.
            MemorySection` has no `compact_at` field to read it from), so this mirrors a sensible
            manifest default the way `hivemind.queen.awake.episode.WAX_CAP_PER_CELL` already
            mirrors `[memory] cell_wax_cap`'s own default.
        handoff_threshold: Fraction past which `intervention_for` orders `HANDOFF` instead;
            defaults to `hivemind.manifest.schema.supervision.DEFAULT_HANDOFF_THRESHOLD`'s own
            value (two thirds of the window, codingrules section 8.9's "threshold reset").
    """

    budget_fraction: float
    output_reserve_tokens: int
    compact_at: float = _DEFAULT_COMPACT_AT
    handoff_threshold: float = _DEFAULT_HANDOFF_THRESHOLD


@dataclass(slots=True)
class Housekeeping:
    """The Queen's own tiny mutable bookkeeping for `hivemind.queen.ticks.housekeeping`'s timer.

    Held on `QueenDeps.housekeeping`, one per Queen: the same "owns its own mutable state in
    place, documented" shape `hivemind.queen.state.ClusterState` already uses for Clustering
    (codingrules section 8.5). Defined here, not in `hivemind.queen.ticks.housekeeping` itself, so
    a real (non-TYPE_CHECKING) import of it from this module never has to run that whole `ticks`
    sub-package's own `__init__` first (every `hivemind.queen.ticks` module already imports
    `QueenDeps` from here at runtime; the reverse edge would cycle).

    Attributes:
        last_sweep_at: When the last House Bee sweep this Queen ran finished, or the Queen's own
            first tick's timestamp before any sweep has actually run (`hivemind.queen.ticks.
            housekeeping.run_housekeeping` seeds this on its very first call rather than running
            a sweep immediately, so a fresh Hive's first tick is never made to pay for one).
        ripener_unbound_warned: Whether `run_housekeeping` has already logged that no
            `ModelSlot.RIPENER` binding could be resolved for a sweep; logged once, not every
            tick, once it first happens.
    """

    last_sweep_at: datetime | None = field(default=None)
    ripener_unbound_warned: bool = field(default=False)


@dataclass(slots=True)
class PlanningLane:
    """The Queen's one in-flight goal plan, run beside her tick rather than inside it (step 10.5).

    Planning a goal is one long model call (seconds to minutes on a local model); run inside the
    tick it would hold every Warden's Heartbeat unread long enough to mark a healthy Warden offline.
    `hivemind.queen.ticks.human.intake` starts at most one plan here at a time, reaps it once done
    on a later tick, and `Queen.stop` reaps it on the way out, so the task always has an owner
    (codingrules section 11). Held on `QueenDeps` like `Housekeeping`, for the same reason.

    Attributes:
        request_id: The goal request being planned right now; None while the lane is free.
        task: The asyncio task planning it; None while the lane is free.
    """

    request_id: str | None = field(default=None)
    task: asyncio.Task[None] | None = field(default=None)


@dataclass(frozen=True, slots=True)
class ProvisionJob:
    """One Virtual Cell being acquired for one ready task, beside the Queen's tick.

    Attributes:
        placement: The Virtual placement being acquired (`ProvisionVirtual` or `ReuseDormant`).
        job: The asyncio task acquiring it (`hivemind.queen.dispatcher.acquire.resolve_link`);
            its result is the Cell's link and the placement actually used, a failed provision
            having been retried once (ADR-0028).
    """

    placement: Placement
    job: asyncio.Task[tuple[WardenLink, Placement]]


@dataclass(slots=True)
class ProvisionLane:
    """The Queen's Virtual Cells being acquired beside her tick, and how many may be at once.

    Provisioning a Virtual Cell takes seconds to minutes (a container or VM boots, its Warden
    dials back); awaited inside the tick it stalled her inbox, liveness and Entrance goals for
    that long. `hivemind.queen.dispatcher.provisions` starts each acquisition here instead, at
    most `limit` at once, and a later dispatch pass collects it: a Cell whose task is still
    waiting is placed, one whose task is gone is released. `Queen.stop` awaits what is still in
    flight, never cancels it (`hivemind.queen.dispatcher.provisions.stop_provisions`). Owns its
    own mutable state in place (codingrules section 8.5), changed only under `DispatchBook.lock`.

    Attributes:
        limit: The most acquisitions in flight at once.
        jobs: Every ready task with a Cell being acquired for it, or acquired and not yet
            placed (its grant still waiting), by task id.
        closed: Set as the Queen stops: she starts no acquisition she would not see finish.
    """

    limit: int = _DEFAULT_PROVISIONS_IN_FLIGHT
    jobs: dict[TaskId, ProvisionJob] = field(default_factory=dict)
    closed: bool = False


@dataclass(slots=True)
class ReleaseLane:
    """The Queen's Virtual Cells being released beside her tick, and how many may be at once.

    Releasing a Virtual Cell asks its Warden to stop (up to 5 s) and then tears the Cell down or
    pauses it; awaited inside the tick it stalled her inbox and liveness for that long, exactly as
    provisioning once did. `hivemind.queen.dispatcher.releases` starts each release here instead,
    at most `limit` running at once (the rest wait their turn on `slots`), and placement offers no
    Cell named in `jobs` to any task while its release runs. `Queen.stop` awaits what is still in
    flight, never cancels it, so the shutdown's retirement never races a release. Owns its own
    mutable state in place (codingrules section 8.5).

    Attributes:
        limit: The most releases running at once; read once, when `slots` is first used.
        jobs: Every Cell with a release started and not yet collected, by Cell id.
        closed: Set as the Queen stops: a release asked for after that runs inline instead.
        slots: Bounds the releases running at once to `limit`; built on first use, inside the
            running loop, so a `QueenDeps` built outside one never holds a loop-bound primitive.
    """

    limit: int = _DEFAULT_RELEASES_IN_FLIGHT
    jobs: dict[CellId, asyncio.Task[None]] = field(default_factory=dict)
    closed: bool = False
    slots: asyncio.Semaphore | None = None

    def releasing(self) -> frozenset[CellId]:
        """Return every Cell whose release is still running: never offered to placement."""
        return frozenset(cell_id for cell_id, job in self.jobs.items() if not job.done())


@dataclass(frozen=True, slots=True)
class BackendHold:
    """One Virtual backend's run of failed provision rounds, and how long it is held back.

    Attributes:
        failures: The rounds in a row whose provision on it failed, the latest included.
        failed_at: When the latest of them failed; an attempt that started before then was
            already in flight, so it belongs to that round, not to a round of its own.
        hold_s: How long placement skips it after the latest, in seconds.
    """

    failures: int
    failed_at: datetime
    hold_s: float

    @property
    def until(self) -> datetime:
        """When placement may choose the backend again, for one provision at a time."""
        return self.failed_at + timedelta(seconds=self.hold_s)


@dataclass(slots=True)
class ProvisionBackoff:
    """The Virtual backends whose provisions keep failing, and how long each is held back.

    A backend that failed every provision was chosen again on every pass that found its task
    waiting, one `cell.provisioning` and `cell.provision_failed` after another.
    `hivemind.queen.dispatcher.backoff` keeps its run of failed rounds here: placement skips it
    for `floor_s` after one, twice as long after each further round in a row, at most `cap_s`,
    and a provision on it that succeeds ends the run. Held in `DispatchBook`, for the same reason
    `GrantWaits` (`hivemind.queen.holds`) is. Owns its own mutable state in place (codingrules
    section 8.5), in memory only: a restarted Queen tries every backend afresh.

    Attributes:
        floor_s: The hold after one failed round, in seconds.
        cap_s: The longest hold, however many rounds fail in a row, in seconds.
        holds: Every backend whose run of failed rounds no success has ended yet, by name.
    """

    floor_s: float = _DEFAULT_BACKOFF_FLOOR_S
    cap_s: float = _DEFAULT_BACKOFF_CAP_S
    holds: dict[str, BackendHold] = field(default_factory=dict)


@dataclass(slots=True)
class DispatchBook:
    """The dispatcher's own runtime bookkeeping, one per Queen: its lock, waits and provisions.

    Owns its own mutable state in place (codingrules section 8.5), in memory only: a restarted
    Queen says each wait afresh rather than trusting a record she did not keep.

    Attributes:
        lock: Serialises every dispatch pass on this Queen, and so every change to `waits`,
            `unplaced` and `provisions`: `Queen.submit_goal`, a finished task and her tick each
            dispatch, and two passes interleaving could both pick the same PENDING task and lose
            the chamber's PENDING -> ASSIGNED race (found by the phase 5 e2e slice).
        waits: Fresh tasks waiting for a grant, and their patience (`GrantWaits`).
        unplaced: Every ready task no Cell could take, by task id, with the cause its one
            `queen.decided` named: said once per cause, never on every pass it keeps waiting.
        provisions: Virtual Cells being acquired beside her tick (`ProvisionLane`).
        releases: Virtual Cells being released beside her tick (`ReleaseLane`).
        backoff: Virtual backends held back after failed provisions (`ProvisionBackoff`),
            changed by the acquisitions themselves, beside her tick.
        held: The Cells whose node is away (roadmap 11.9): the dispatcher neither retries, resumes
            nor places on one, and the Queen does not fail its tasks (`HeldCells`); the swarm gate
            holds and releases them.
        resumes: Where each re-placed task resumes from at its next placement (`ResumeHints`).
        placed_on: Every Warden she has placed a task on, recorded before the placement is
            written: what a stop gives back is what is still placed on one of these
            (`hivemind.queen.dispatcher.ready.unfinished`), never another Queen's work on the
            same Brood Chamber. Grows by one id per Warden (the Hive Stand's, one per Virtual
            Cell), and is never pruned: a task can outlive its Warden's link.
        returned: The Wardens back from a cut whose first settled Heartbeat she has not read
            yet: that one says whether the Warden clustered fully while it was away, and so
            holds tasks only she can start again (`hivemind.queen.cluster.returned`).
        watch: The watch requests (ADR-0069): a task that would drive the running screen or
            audio waits here for the owner's answer from the Hive Stand (`WatchBook`).
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waits: GrantWaits = field(default_factory=GrantWaits)
    unplaced: dict[TaskId, str] = field(default_factory=dict)
    provisions: ProvisionLane = field(default_factory=ProvisionLane)
    releases: ReleaseLane = field(default_factory=ReleaseLane)
    backoff: ProvisionBackoff = field(default_factory=ProvisionBackoff)
    held: HeldCells = field(default_factory=HeldCells)
    resumes: ResumeHints = field(default_factory=ResumeHints)
    placed_on: set[WardenId] = field(default_factory=set)
    returned: set[WardenId] = field(default_factory=set)
    watch: WatchBook = field(default_factory=WatchBook)


def _set_event() -> asyncio.Event:
    """Build the Queen's wake signal already set, so her first tick drains what a crash left."""
    event = asyncio.Event()
    event.set()
    return event


@dataclass(frozen=True, slots=True)
class QueenDeps:
    """Every collaborator the Queen is built with; manifest slices only, never a HiveManifest.

    Attributes:
        chamber: The Brood Chamber: the Queen's one door onto the task graph.
        memory: Where the Queen reads and writes Pins, Notes, Handoffs and episodes.
        trail: The Pheromone Trail the Queen records every `queen.*` event to.
        identity: The Hive, node and actor the Queen stamps on every memory write she makes.
        clock: Injected time source for every id minted, every timestamp written and every sleep.
        policy: The Queen's own escalation playbook, read for every Alarm she handles.
        bound_for: Resolves a `ModelSlot` (its own manifest key) to a live `BoundModel`, walking
            its fallback chain (`hivemind.llm.slots.resolve`, closed over the Queen's own provider
            lookup, `bindings` and `map`).
        rebind: Resolves a named `[llm.slots]` binding key, for the slot it is recorded as
            serving, to a fresh `BoundModel` (`hivemind.llm.slots.resolve_key`); used for an
            awake-chosen effort override and for walking a fallback chain.
        bindings: Every `[llm.slots]` row, forage-side; the fallback chain `bound_for`/`rebind`
            walk and the source `hivemind.queen.dispatcher` reads a task's own binding key from.
        call_gate: The seat meter every model call passes through; a `FannerLane` in production,
            `hivemind.llm.DirectCallGate()` in tests.
        map: The Forage map: every source that can serve a model, and its live figures.
        budgets: The goal-level spend, token and sub-bee caps `hivemind.forage.allocate.grant`
            draws every fresh grant's ceilings from.
        heartbeat_interval_s: How often the Queen expects each attached Warden's own Heartbeat.
        heartbeat_miss_limit: Missed heartbeats before a Warden is marked offline.
        alarm_attempt_limit: A ceiling on attempts before an Alarm escalates to the human
            regardless of what the escalation policy's own rows would otherwise decide.
        memory_budget: The `[memory]` slice an awake episode's `TokenBudget` is built from.
        scratch_root: The Hive Stand's own `[hive_stand] scratch_root`, resolved (roadmap step
            5.0b). Read by `submit_goal` (a plan declaring a leaving inside it is refused) and
            by `hivemind.queen.authority` to fill a Warden's `{scratch}` entries (step 10.3).
        enforcer: The Guard's adapter every one of the Queen's enforcement points calls (roadmap
            step 10.3, ADR-0039): placement, grant issue, Forage requests, Warden spawn, question
            routing and Comb Shield egress. Its policy is the one every set she computes is built
            from, and it records each refusal as `guard.denied` on this Queen's own trail.
        goal_requests: The durable goal-request table (roadmap step 10.5, ADR-0040): what
            `Queen.request_goal` commits and her intake drain plans (`hivemind.queen.intake`).
        chat: The chat log, the human end of her inbox (`hivemind.queen.chat`): human messages
            in, her replies, questions and Alarms out.
        footprints: Every `[forage.roles.<role>]` footprint, forage-side, keyed by
            `waggle.messages.task.WorkerRole`; `hivemind.queen.dispatcher` reads
            `footprints[WorkerRole.DRONE]` for every fresh grant it computes (roadmap step 3.21,
            second half). Defaults to a single DRONE entry.
        reserve: The `[forage.reserve]` Royal Reserve every fresh grant subtracts first; defaults
            to `RoyalReserve()`.
        grant_ttl_s: The `[forage] grant_ttl_s` every fresh grant expires after (default 300.0).
        ledger: The Queen's live book of Forage (roadmap step 4.7): every Cell's latest
            capacity, every live shared grant and the headroom they leave. Defaults to a fresh,
            in-memory `ForageLedger()`, so a caller that never names it still builds.
        orders: Roadmap step 4.9 (Clustering): the durable `hive cluster`/`hive wake` rows
            `hivemind.queen.cluster.tick.run_cluster_tick` polls every tick; defaults to a fresh
            `InMemoryOrderStore()`.
        cluster_state: The Queen's own mode and the set of clustered providers
            (`hivemind.queen.state.ClusterState`), read by `run_cluster_tick` and by
            `awake_available` before every awake episode. Defaults to RUNNING with none.
        health_poller: The clustered-provider health-probe schedule (`hivemind.queen.cluster.
            health.HealthPoller`), on its own backoff. Defaults to a fresh instance over the
            module's own default `ClusterBackoff`.
        provider_lookup: Resolves a `[llm.providers.<name>]` key to a live `LLMProvider`, for
            `HealthPoller.probe`; `ProviderRegistry.provider` bound to an instance in production.
            None (the default) skips health probing.
        housekeeping: The Queen's own `last_sweep_at` bookkeeping (roadmap step 4.3); read and
            advanced by `hivemind.queen.ticks.housekeeping.run_housekeeping`.
        sweep_interval_s: The manifest's own `[memory] sweep_interval_s`, the cadence
            `hivemind.workers.roles.house_bee.SweepSchedule` reads to decide when the next sweep
            is due. Defaults to `DEFAULT_SWEEP_INTERVAL_S`'s own value (one hour).
        hot_window_s: The manifest's own `[memory] hot_window_s`, the compaction cutoff a
            Queen-run sweep measures entry age against. Defaults to `DEFAULT_HOT_WINDOW_S`'s own
            value (four hours).
        placement_policy: The `[placement]`/`[virtual_cells]`-derived value `hivemind.queen.
            placement.decide.decide` reads (roadmap step 5.7, ADR-0028). Defaults to
            `PlacementPolicy()` (`prefer="real"`, `allow_hive_stand=True`, no template).
        virtual_backends: Every registered Virtual backend with room to provision, read by
            `hivemind.queen.dispatcher`'s snapshot helper to build `decide`'s own `Inventory`.
        dormant_cells: Every Overwintered Virtual Cell available to resume instead of a fresh
            provision (`docs/adr/0029`).
        virtual_provider: The seam `hivemind.queen.dispatcher` calls to turn a `ProvisionVirtual`/
            `ReuseDormant` Placement into a `WardenLink` (`VirtualCellProvider`, defined above).
            `None` (the default) means a Virtual Placement is a `PlacementError`.
        virtual_backend_source: Feeds `virtual_backends` live from a `CellLifecycle`, when set.
        dormant_cell_source: The same live-feed seam as `virtual_backend_source`, for dormant ones.
        on_task_finished: Told, beside the tick (`dispatcher.releases`), of every finished task's
            Cell (`CellId`, `TaskOutcome`) and of a Cell acquired for a task cancelled before it
            ran there; `None` is a no-op, `cell_gate.release.make_on_task_finished` the real one.
        dispatch: The dispatcher's own lock, waits, provisions and releases (`DispatchBook`).
        keep_root: `[hive_stand] keep_root`, resolved (step 5.0e); None until set. Read only by
            `submit_goal`, which tells the planner (`PlanBrief.keep_root`) to leave files under it.
        honey: The Hive's Honey Store handles (roadmap phase 7, built by `hivemind.cli.compose.
            honey`): intake, the retriever for a bee's query and her own pre-check. None (the
            default) wires no Honey Store: a query is answered empty with that reason, a deposit
            is refused with `control.error`, and no pre-check runs.
        royal_jelly: The Lab's and the Basket's handles (phase 9); None wires neither.
        sandboxes: Provisions the Comb's sandbox Cell; None leaves only the subprocess sandbox.
        tool_lane: Her tool jobs and Basket transfers beside the tick (`ToolLane`).
        uplink_keys: The key a Warden's node signs a Basket manifest with; None trusts the Hive key.
        nurse_max_rounds: `[nurse] max_rounds`: Nurse rejections sent back before one escalates.
        requeening: Requeening's state (ADR-0062): its store, cadences and any recovery settling.
        supersedure: Supersedure's wiring and the one move under way, if any (ADR-0065).
        human_channel: How the Queen tells the human's devices something is waiting (a reply, a
            question, an Alarm, a goal request's end); `NullHumanChannel` tells nobody.
        wake: The in-process signal her tick awaits beside her Warden links (ADR-0040), set by a
            goal request, a human message or a finished plan; it starts set, so her first tick
            drains whatever rows a crash left behind.
        planning: Her one in-flight goal plan (`PlanningLane`).
        scanner: The untrusted-content scanner (roadmap step 10.6b, ADR-0043) a human's chat
            words pass through before her awake episode reads them (`hivemind.queen.ticks.awake.
            scan_human_text`); the composition root's, keyed from the Hive's secret store. Defaults
            to the shipped patterns and thresholds with an in-memory key.
        guard_bee: The Guard Bee her tick runs (step 10.6, `build_guard_bee`); None runs none.
        in_process_providers: The `[llm.providers]` names whose model runs inside whichever
            process binds it (`hivemind.llm.registry.runs_in_process`), so a grant binding on
            one of them is local to the Cell it is issued for, like a source that Cell serves
            itself (roadmap step 10.3a: a Night Veil grant names local bindings only). Defaults
            to none, so a Night Veil grant fails closed unless a composition root states them.
        on_heartbeat: Handed every Heartbeat she records, its one way out (it never reaches the
            trail): the Hive Entrance's telemetry board in `hive serve`; None (nobody) by default.
        intake_lock: Serialises her goal-request edges (`hivemind.queen.intake.writes`): intake, a
            plan landing beside her tick and a revocation each move the row as it stands.
        guard: Her Guard requests, dire patterns, egress seam and pause bound (roadmap step
            10.6a, ADR-0043); an in-memory table and the shipped patterns by default.
    """

    chamber: BroodChamber
    memory: MemoryStore
    trail: PheromoneTrail
    identity: MemoryIdentity
    clock: Clock
    policy: EscalationPolicy
    bound_for: Callable[[ModelSlot], BoundModel]
    rebind: Callable[[str, ModelSlot], BoundModel]
    bindings: tuple[SlotBinding, ...]
    call_gate: CallGate
    map: ForageMap
    budgets: GoalBudgets
    heartbeat_interval_s: float
    heartbeat_miss_limit: int
    alarm_attempt_limit: int
    memory_budget: MemoryBudget
    scratch_root: Path
    enforcer: Enforcer
    goal_requests: GoalRequestStore
    chat: ChatLog
    footprints: Mapping[WorkerRole, RoleFootprint] = field(
        default_factory=lambda: {WorkerRole.DRONE: _DEFAULT_DRONE_FOOTPRINT}
    )
    reserve: RoyalReserve = field(default_factory=RoyalReserve)
    grant_ttl_s: float = _DEFAULT_GRANT_TTL_S
    ledger: ForageLedger = field(default_factory=ForageLedger)
    # Roadmap step 4.9 (Clustering): defaulted, so a QueenDeps built without them still builds.
    orders: OrderStore = field(default_factory=InMemoryOrderStore)
    health_poller: HealthPoller = field(default_factory=HealthPoller)
    provider_lookup: ProviderLookup | None = None
    # Held here rather than on the Queen instance so `hive cluster`/`hive wake` and the tests
    # can read her mode without reaching into the kernel, and so queen.py stays inside its
    # size cap (codingrules 5.1); exactly one per Queen, like every other mutable store here.
    cluster_state: ClusterState = field(default_factory=ClusterState)
    # Roadmap step 4.3 (the House Bee sweep on the Queen's own timer): defaulted, like those above.
    housekeeping: Housekeeping = field(default_factory=Housekeeping)
    sweep_interval_s: float = _DEFAULT_SWEEP_INTERVAL_S
    hot_window_s: float = _DEFAULT_HOT_WINDOW_S
    # Roadmap step 5.7 (ADR-0028): additive and defaulted, so a QueenDeps built without them
    # places every task on the Real side alone, exactly as before the Virtual side existed.
    placement_policy: PlacementPolicy = field(default_factory=PlacementPolicy)
    virtual_backends: tuple[VirtualBackendCandidate, ...] = field(default_factory=tuple)
    dormant_cells: tuple[DormantCandidate, ...] = field(default_factory=tuple)
    virtual_provider: VirtualCellProvider | None = None
    # Roadmap 5.6/5.9 (live feeds): defaulted to None, so the static tuples above stay in force.
    virtual_backend_source: VirtualBackendSource | None = None
    dormant_cell_source: DormantCellSource | None = None
    on_task_finished: OnTaskFinished | None = None
    on_cell_granted: OnCellGranted | None = None
    # One per Queen, like `housekeeping`: every dispatch pass serialises on its lock, and its waits
    # and provisions outlive any one pass (DispatchBook's own docstring).
    dispatch: DispatchBook = field(default_factory=DispatchBook)
    keep_root: Path | None = None  # Roadmap step 5.0e: the planner's, never TaskAssign's.
    # Roadmap step 10.5 (ADR-0040): the human end. Defaulted so a composition root that wires no
    # Hive Entrance (hive run, every test) tells nobody, and so the Queen's own mutable wake and
    # planning bookkeeping live here beside `housekeeping`, one per Queen.
    human_channel: HumanChannel = field(default_factory=NullHumanChannel)
    wake: asyncio.Event = field(default_factory=_set_event)
    planning: PlanningLane = field(default_factory=PlanningLane)
    # Roadmap step 10.6b: defaulted so every QueenDeps built without one still scans chat words.
    scanner: ContentScanner = field(default_factory=default_content_scanner)
    # Roadmap step 10.3a: additive and defaulted to none, so a Night Veil grant fails closed.
    in_process_providers: frozenset[str] = frozenset()
    # Roadmap step 10.5 (the Entrance's read side): defaulted; one intake lock per Queen.
    on_heartbeat: OnHeartbeat | None = None
    intake_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    guard_bee: GuardBee | None = None  # Roadmap step 10.6: additive; None runs as before.
    guard: GuardDeps = field(default_factory=GuardDeps)  # Roadmap step 10.6a (ADR-0043).
    honey: HoneyAccess | None = None  # Roadmap phase 7: None keeps every hand-built one working.
    routing_rules: RoutingRules | None = None  # Roadmap 8.1: [llm.floors] for hosting plans.
    royal_jelly: RoyalJellyAccess | None = None  # Roadmap phase 9: None keeps hand-built ones.
    # Phase 9 (ADR-0047): the sandbox seam, the tool lane and the manifest-key lookup; defaulted.
    sandboxes: SandboxCellProvider | None = None
    tool_lane: ToolLane = field(default_factory=ToolLane)
    uplink_keys: UplinkKeys | None = None
    nurse_max_rounds: int = _DEFAULT_NURSE_MAX_ROUNDS  # ADR-0061; defaulted like the rest.
    requeening: RequeeningBook = field(default_factory=RequeeningBook)  # ADR-0062.
    supersedure: SupersedureBook = field(default_factory=SupersedureBook)  # ADR-0065.
