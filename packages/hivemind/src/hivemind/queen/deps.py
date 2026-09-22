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
prompt is budgeted against (`memory_budget`), and, roadmap step 5.7 (ADR-0028), her placement
inputs: `placement_policy`, the Virtual side's own inventory (`virtual_backends`,
`dormant_cells`), and the seam that turns a Virtual `Placement` into a `WardenLink`
(`virtual_provider`, a `VirtualCellProvider`, defined here beside `WardenLink` rather than in
`hivemind.queen.placement` since it names `WardenLink`/`Task` and that package must never import
this one back). `WardenLink` is the Queen-side half of one attached Warden's own Waggle link:
`hivemind.wardens.deps.WardenDeps.queen_link`/`.hop` is the Warden's own end of the exact same
pair.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Built once per Queen by whichever
    composition root constructs one -- the CLI (roadmap step 3.21) in production,
    `tests.builders.queen.make_queen_deps` in tests -- which attaches each `WardenLink` with
    `Queen.attach_warden` before `run()`. Calls into `hivemind.brood_chamber`, `hivemind.forage`,
    `hivemind.llm.ladders.gate`, `hivemind.llm.slots`, `hivemind.memory`, `hivemind.pheromone`,
    `hivemind.queen.cluster.health`/`.orders` (HealthPoller, OrderStore, InMemoryOrderStore --
    roadmap step 4.9), `hivemind.queen.forage.ledger` (ForageLedger), `hivemind.queen.placement`
    (PlacementPolicy, VirtualBackendCandidate, DormantCandidate, Placement -- roadmap step 5.7),
    `hivemind.supervision` and waggle only.

Key invariants:
    - `QueenDeps` and `WardenLink` are frozen and slotted (codingrules section 8.5): neither is
      itself read from or written to JSON/TOML, so both are dataclasses, not pydantic BaseModels,
      matching `WardenDeps`.
    - `QueenDeps` carries manifest *slices* only, never a `HiveManifest` itself (codingrules
      section 13): the composition root converts.
    - `bound_for` is scoped to whichever `ModelSlot` a caller asks for (`QUEEN`, `ATTENDANT`, or a
      task's own slot for a fresh binding); `rebind` walks the same `bindings` chain from a named
      key instead of a slot's own manifest key, for a REBIND within the fallback chain.

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit this bundle exists to keep.
    - .claude/codingrules.md section 13 for "the Queen takes manifest slices, never a HiveManifest".
    - docs/adr/0019-queen-kernel-autopilot-first-with-stateless-awake-episodes.md for why the Queen
      holds no session and no registry.
    - hivemind.wardens.deps for WardenDeps, the bundle this one is modelled on.
    - hivemind.queen.queen for Queen, this bundle's one consumer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from hivemind.brood_chamber import BroodChamber, Task, TaskOutcome
from hivemind.cell import Cell
from hivemind.forage import (
    ForageMap,
    GoalBudgets,
    ModelSlot,
    RoleFootprint,
    RoyalReserve,
    SlotBinding,
)
from hivemind.llm import BoundModel, CallGate, ProviderLookup
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.queen.cluster.health import HealthPoller
from hivemind.queen.cluster.orders import InMemoryOrderStore, OrderStore
from hivemind.queen.forage.ledger import ForageLedger
from hivemind.queen.placement import (
    DormantCandidate,
    Placement,
    PlacementPolicy,
    VirtualBackendCandidate,
)
from hivemind.queen.state import ClusterState
from hivemind.supervision import EscalationPolicy
from waggle.clock import Clock
from waggle.envelope import Hop
from waggle.ids import CellId, GrantId, WardenId
from waggle.messages.task import WorkerRole
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
# roadmap step 4.6: MemoryBudget's own two threshold defaults, additive (module docstring on the
# class itself explains why they are not yet read from a manifest slice). 0.5 leaves real room
# before handoff_threshold's own two-thirds mark, so a bee that only needs compacting is caught
# before it is forced to hand off entirely.
_DEFAULT_COMPACT_AT = 0.5
_DEFAULT_HANDOFF_THRESHOLD = 0.66  # Matches manifest.schema.supervision.DEFAULT_HANDOFF_THRESHOLD.
# roadmap step 4.3's own wiring step: mirrors manifest.schema.supervision's own
# DEFAULT_SWEEP_INTERVAL_S/DEFAULT_HOT_WINDOW_S so a QueenDeps built without naming either field
# (every pre-housekeeping test) still runs a House Bee sweep on a sensible cadence.
_DEFAULT_SWEEP_INTERVAL_S = 3_600.0  # One hour.
_DEFAULT_HOT_WINDOW_S = 4.0 * 3600.0  # Four hours.

__all__ = [
    "DormantCellSource",
    "Housekeeping",
    "MemoryBudget",
    "OnCellGranted",
    "OnTaskFinished",
    "QueenDeps",
    "VirtualBackendSource",
    "VirtualCellProvider",
    "WardenLink",
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
    """

    warden_id: WardenId
    cell: Cell
    transport: Transport
    hop: Hop


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
        footprints: Every `[forage.roles.<role>]` footprint, forage-side, keyed by
            `waggle.messages.task.WorkerRole`; `hivemind.queen.dispatcher` reads
            `footprints[WorkerRole.DRONE]` for every fresh grant it computes (roadmap step 3.21,
            second half). Defaults to a single DRONE entry matching the constant the dispatcher
            used before this field existed.
        reserve: The `[forage.reserve]` Royal Reserve every fresh grant subtracts first. Defaults
            to `RoyalReserve()` (its own manifest-sensible defaults), matching the dispatcher's
            prior module constant.
        grant_ttl_s: The `[forage] grant_ttl_s` every fresh grant expires after. Defaults to
            300.0, matching the dispatcher's prior module constant.
        ledger: The Queen's live book of Forage (roadmap step 4.7): every Cell's latest
            capacity, every live shared grant and the headroom they leave. Defaults to a fresh,
            in-memory-only `ForageLedger()` sharing `reserve`'s own default, so a caller that
            never names this field (every pre-4.7 test) still builds a valid QueenDeps.
        orders: Roadmap step 4.9 (Clustering): the durable `hive cluster`/`hive wake` rows
            `hivemind.queen.cluster.tick.run_cluster_tick` polls every tick. Defaults to a fresh
            `InMemoryOrderStore()`, matching every other roadmap-4.9-and-earlier test.
        cluster_state: The Queen's own mode and the set of clustered providers
            (`hivemind.queen.state.ClusterState`), read by `run_cluster_tick` and by
            `awake_available` before every awake episode. Defaults to RUNNING with none.
        health_poller: The clustered-provider health-probe schedule (`hivemind.queen.cluster.
            health.HealthPoller`), on its own backoff. Defaults to a fresh instance over the
            module's own default `ClusterBackoff`.
        provider_lookup: Resolves a `[llm.providers.<name>]` key to a live `LLMProvider`, for
            `HealthPoller.probe`; `ProviderRegistry.provider` bound to an instance in production.
            None (the default) skips health probing entirely rather than raising, since a caller
            that never names this field has no registry to probe with in the first place.
        housekeeping: The Queen's own `last_sweep_at` bookkeeping (roadmap step 4.3's own wiring
            step); read and advanced by `hivemind.queen.ticks.housekeeping.run_housekeeping`.
            Defaults to a fresh `Housekeeping()` (no sweep run yet), matching every field here.
        sweep_interval_s: The manifest's own `[memory] sweep_interval_s`, the cadence
            `hivemind.workers.roles.house_bee.SweepSchedule` reads to decide when the next sweep
            is due. Defaults to `DEFAULT_SWEEP_INTERVAL_S`'s own value (one hour).
        hot_window_s: The manifest's own `[memory] hot_window_s`, the compaction cutoff a
            Queen-run sweep measures entry age against. Defaults to `DEFAULT_HOT_WINDOW_S`'s own
            value (four hours).
        placement_policy: The `[placement]`/`[virtual_cells]`-derived value `hivemind.queen.
            placement.decide.decide` reads (roadmap step 5.7, ADR-0028). Defaults to
            `PlacementPolicy()` (`prefer="real"`, `allow_hive_stand=True`, no template), matching
            v0's own Real-only behaviour.
        virtual_backends: Every registered Virtual backend with room to provision, read by
            `hivemind.queen.dispatcher`'s snapshot helper to build `decide`'s own `Inventory`.
            Empty until roadmap step 5.6 gives a composition root something to populate it with.
        dormant_cells: Every Overwintered Virtual Cell available to resume instead of a fresh
            provision (`docs/adr/0029`). Empty until roadmap step 5.9 (the Overwintering pool)
            gives a composition root something to populate it with.
        virtual_provider: The seam `hivemind.queen.dispatcher` calls to turn a `ProvisionVirtual`/
            `ReuseDormant` Placement into a `WardenLink` (`VirtualCellProvider`, defined above).
            `None` (the default) means a Virtual Placement is a `PlacementError` instead of being
            acquired; roadmap step 5.6 is the first to inject a real one.
        virtual_backend_source: Feeds `virtual_backends` live from a running `hivemind.hive.
            lifecycle.CellLifecycle` instead of the static tuple above, when set.
            `hivemind.queen.dispatcher.snapshot.build_inventory` calls this (converting the
            hive-layer candidates it returns into `queen.placement.inventory` ones) instead of
            reading `virtual_backends` directly, whenever it is not `None`. `None` (the default)
            keeps every pre-5.6 test's own static-tuple behaviour.
        dormant_cell_source: The same live-feed seam as `virtual_backend_source`, for
            `dormant_cells`.
        on_task_finished: Told about every finished task's own Cell (`CellId`, its
            `hivemind.brood_chamber.TaskOutcome`), unconditionally, by `hivemind.queen.ticks.
            results.complete_task`. `None` (the default) is a no-op; `hivemind.queen.cell_gate.
            release.make_on_task_finished` builds the real implementation, over a `CellLifecycle`,
            which itself keys off whether that lifecycle recognises the Cell at all -- this field
            is how a Virtual Cell's own release/overwinter/teardown gets triggered without the
            Queen ever reading `cell.kind` outside placement (codingrules section 8.7).
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
    footprints: Mapping[WorkerRole, RoleFootprint] = field(
        default_factory=lambda: {WorkerRole.DRONE: _DEFAULT_DRONE_FOOTPRINT}
    )
    reserve: RoyalReserve = field(default_factory=RoyalReserve)
    grant_ttl_s: float = _DEFAULT_GRANT_TTL_S
    ledger: ForageLedger = field(default_factory=ForageLedger)
    # Roadmap step 4.9 (Clustering): additive fields, every one defaulted so a QueenDeps built
    # before this dispatch (every existing test) keeps building unchanged.
    orders: OrderStore = field(default_factory=InMemoryOrderStore)
    health_poller: HealthPoller = field(default_factory=HealthPoller)
    provider_lookup: ProviderLookup | None = None
    # Held here rather than on the Queen instance so `hive cluster`/`hive wake` and the tests
    # can read her mode without reaching into the kernel, and so queen.py stays inside its
    # size cap (codingrules 5.1); exactly one per Queen, like every other mutable store here.
    cluster_state: ClusterState = field(default_factory=ClusterState)
    # Roadmap step 4.3's own wiring step (the House Bee sweep on the Queen's own timer): additive
    # fields, every one defaulted so a QueenDeps built before this dispatch (every existing test)
    # keeps building unchanged.
    housekeeping: Housekeeping = field(default_factory=Housekeeping)
    sweep_interval_s: float = _DEFAULT_SWEEP_INTERVAL_S
    hot_window_s: float = _DEFAULT_HOT_WINDOW_S
    # Roadmap step 5.7 (ADR-0028): additive fields, every one defaulted so a QueenDeps built
    # before this dispatch (every existing test) keeps placing every task on the Real side alone,
    # exactly as before. `virtual_backends`/`dormant_cells` stay empty until roadmap step 5.6 (the
    # Virtual Cell lifecycle) and step 5.9 (the Overwintering pool) give a composition root
    # something real to populate them with.
    placement_policy: PlacementPolicy = field(default_factory=PlacementPolicy)
    virtual_backends: tuple[VirtualBackendCandidate, ...] = field(default_factory=tuple)
    dormant_cells: tuple[DormantCandidate, ...] = field(default_factory=tuple)
    virtual_provider: VirtualCellProvider | None = None
    # This dispatch's own reconciliation (roadmap 5.6/5.9): additive, every one defaulted to None
    # so every earlier test's own static-tuple QueenDeps keeps building and behaving unchanged.
    virtual_backend_source: VirtualBackendSource | None = None
    dormant_cell_source: DormantCellSource | None = None
    on_task_finished: OnTaskFinished | None = None
    on_cell_granted: OnCellGranted | None = None
    # Serialises every dispatch_ready call on this Queen. Queen.submit_goal dispatches directly and
    # the tick loop dispatches again on every inbox item; while a Virtual placement awaits a real
    # provision inside resolve_link, the other call site could otherwise pick the same still-PENDING
    # task and lose the chamber's PENDING -> ASSIGNED race (found by the phase 5 e2e slice).
    dispatch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
