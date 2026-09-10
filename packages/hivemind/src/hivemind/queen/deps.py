"""Define WardenLink and QueenDeps: every collaborator, and every attached Warden's own wire.

`hivemind.queen.queen.Queen.__init__` takes a `WardenId` per attached link and one `QueenDeps`
bundle (codingrules section 5.1: "introduce a frozen dataclass for the argument group"), the same
role `hivemind.wardens.deps.WardenDeps` plays for a `Warden`. `QueenDeps` carries every collaborator
the Queen's tick touches: her task store (`chamber`), her hot-state and durable memory
(`memory`, `identity`, `clock`), her audit sink (`trail`), her escalation playbook
(`policy`, `alarm_attempt_limit`), how she resolves and rebinds a model slot without ever holding
a `HiveManifest` (`bound_for`, `rebind`, `bindings`, `call_gate`, `map`), her share of Forage
(`budgets`), her liveness cadence (`heartbeat_interval_s`, `heartbeat_miss_limit`) and the slice of
`[memory]` an awake episode's prompt is budgeted against (`memory_budget`). `WardenLink` is the
Queen-side half of one attached Warden's own Waggle link: `hivemind.wardens.deps.WardenDeps.
queen_link`/`.hop` is the Warden's own end of the exact same pair.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Built once per Queen by whichever
    composition root constructs one -- the CLI (roadmap step 3.21) in production,
    `tests.builders.queen.make_queen_deps` in tests -- which attaches each `WardenLink` with
    `Queen.attach_warden` before `run()`. Calls into `hivemind.brood_chamber`, `hivemind.forage`,
    `hivemind.llm.ladders.gate`, `hivemind.llm.slots`, `hivemind.memory`, `hivemind.pheromone`,
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

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from hivemind.brood_chamber import BroodChamber
from hivemind.cell import Cell
from hivemind.forage import (
    ForageMap,
    GoalBudgets,
    ModelSlot,
    RoleFootprint,
    RoyalReserve,
    SlotBinding,
)
from hivemind.llm import BoundModel, CallGate
from hivemind.memory import MemoryIdentity, MemoryStore
from hivemind.pheromone import PheromoneTrail
from hivemind.supervision import EscalationPolicy
from waggle.clock import Clock
from waggle.envelope import Hop
from waggle.ids import WardenId
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

__all__ = ["MemoryBudget", "QueenDeps", "WardenLink"]


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


@dataclass(frozen=True, slots=True)
class MemoryBudget:
    """The slice of the manifest's `[memory]` section an awake episode's prompt is sized against.

    Attributes:
        budget_fraction: Fraction of a bound model's context window reserved for assembled
            content (`[memory] budget_fraction`).
        output_reserve_tokens: Tokens reserved for the model's own reply, never spent on
            assembled content (`[memory] output_reserve_tokens`).
    """

    budget_fraction: float
    output_reserve_tokens: int


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
