"""Define GrantInputs and grant(): the pure v0 Forage allocator.

`grant` is pure in the sense codingrules section 8.3 asks of a decision function: plain data in,
a plain `ForageGrant` out, no I/O, and the same inputs always produce the same grant. It computes
`max_sub_bees` as the minimum of the Cell's own cap, free memory over the role's footprint memory,
free cores over its cpu, reachable model seats, and the goal's remaining bee cap (roadmap step
3.12); `allowed` bindings are the Forage map sources whose grade clears the task's tempo floor
(`hivemind.forage.tempo.grade_floor`) and whose cost fits the spend budget; the Royal Reserve is
subtracted before any of that, and a further headroom margin shaves a percentage off whatever
remains, so a grant never runs right up to the edge of what a capacity report might be slightly
stale about. `GrantInputs` groups every input into one frozen dataclass (codingrules section
8.2's four-collaborator rule and section 5.1's five-parameter limit both push a function with this
many inputs toward one grouping value rather than a long parameter list); `GoalBudgets` is the
small slice of the manifest's `[forage]` section (spend cap, token budget, sub-bee cap per goal)
`grant` needs, defined here rather than imported from `hivemind.manifest` because Layer 1 may not
import Layer 2 (codingrules section 4).

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Called by the Queen's dispatcher
    (`hivemind.queen.dispatcher`, a later phase 3 step) when it issues a grant to a Warden. Calls
    into `hivemind.forage.errors`, `hivemind.forage.grant_state`, `hivemind.forage.map`,
    `hivemind.forage.models`, `hivemind.forage.slots`, `hivemind.forage.tempo` and
    `waggle.messages.task` (`WorkerRole`) only.

Key invariants:
    - grant() never mutates `inputs.map`; it only reads `ForageMap.sources()`.
    - The returned ForageGrant's max_sub_bees never exceeds `inputs.cell_capacity.max_sub_bees`,
      `inputs.budgets.max_sub_bees`, or what the Cell's free memory allows once
      `inputs.reserve.memory_bytes` is subtracted (property-tested with hypothesis).
    - Every AllowedBinding grant() returns names a source whose grade is at least
      `grade_floor(inputs.tempo.accuracy)`.
    - v0 names every allowed binding under `ModelSlot.WORKER`; per-role slot mapping and per-slot
      hosting plans (`hivemind.forage.models.pools.HostingPlan`) are the Queen's job, a later
      phase 3 step this allocator does not attempt.

See Also:
    - .claude/roadmap.md step 3.12 for the allocator's exact formula.
    - .claude/codingrules.md section 8.10 for grants, the Royal Reserve and the two pools.
    - hivemind.forage.tempo for grade_floor, the grade-floor-by-accuracy-bar table.
    - hivemind.forage.map for ForageMap, one of GrantInputs' fields.
    - hivemind.forage.grant_state for GrantState, the state every fresh grant starts in.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from hivemind.forage.errors import AllocationError
from hivemind.forage.grant_state import GrantState
from hivemind.forage.map import ForageMap
from hivemind.forage.models import (
    AllowedBinding,
    ForageCapacity,
    ForageGrant,
    HostCapacity,
    ModelSource,
    RoleFootprint,
    RoyalReserve,
    SeatReservation,
)
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo, grade_floor
from waggle.ids import CellId, GrantId, TaskId, WardenId
from waggle.messages.task import WorkerRole

# A footprint dimension with zero cost (e.g. a role that never touches the GPU) imposes no ceiling
# on that dimension; a real cap elsewhere in the min() always ends up binding instead.
_UNBOUNDED_SUB_BEES = 1_000_000_000

# The most effort a grant allows per tempo accuracy bar: cheap and fast for urgent, low-stakes
# work; effort has no rung above HIGH, so CRITICAL (already pushed onto the strongest sources by
# its grade floor) shares HIGH's ceiling rather than needing a rung that does not exist.
_EFFORT_CEILINGS: dict[AccuracyBar, Effort] = {
    AccuracyBar.LOW: Effort.LOW,
    AccuracyBar.NORMAL: Effort.MEDIUM,
    AccuracyBar.HIGH: Effort.HIGH,
    AccuracyBar.CRITICAL: Effort.HIGH,
}

__all__ = ["GoalBudgets", "GrantInputs", "grant"]


@dataclass(frozen=True, slots=True)
class GoalBudgets:
    """The slice of the manifest's `[forage]` section one grant's caps are drawn from.

    Attributes:
        spend_cap_usd: The goal's remaining spend cap, in US dollars
            (`[forage] spend_cap_per_goal_usd`).
        token_budget: The goal's remaining token budget (`[forage] token_budget_per_goal`).
        max_sub_bees: The goal's remaining cap on concurrent sub-bees
            (`[forage] max_sub_bees_per_goal`).
    """

    spend_cap_usd: float
    token_budget: int
    max_sub_bees: int


@dataclass(frozen=True, slots=True)
class GrantInputs:
    """Every input `grant` needs, grouped into one value (codingrules sections 5.1 and 8.2).

    Attributes:
        cell_capacity: The holder's Cell's reported capacity.
        role: The Worker role the grant's sub-bees will run (`waggle.messages.task.WorkerRole`);
            used to size nothing here directly (that is `footprint`'s job) but recorded in the
            grant's reason.
        footprint: What one bee of `role` costs its Cell while running.
        tempo: The requesting task's speed-against-accuracy setting.
        map: The Forage map to draw allowed bindings from.
        reserve: What the Queen holds back from the shared pool before this grant.
        budgets: The goal's remaining spend, token and sub-bee caps.
        holder: The Warden the grant is issued to.
        cell_id: The Cell the holder runs.
        task_id: The task whose caps sized `budgets`; None for a standing grant.
        grant_id: The id to mint this grant under; minted by the caller (`waggle.ids.
            new_grant_id`) rather than here, so `grant` stays free of the clock and randomness a
            fresh id needs and remains a pure function of its inputs.
        now: The current time, for `expires_at`.
        ttl_s: How many seconds until the grant expires unless renewed by heartbeat.
    """

    cell_capacity: ForageCapacity
    role: WorkerRole
    footprint: RoleFootprint
    tempo: Tempo
    map: ForageMap
    reserve: RoyalReserve
    budgets: GoalBudgets
    holder: WardenId
    cell_id: CellId
    task_id: TaskId | None
    grant_id: GrantId
    now: datetime
    ttl_s: float


def grant(inputs: GrantInputs) -> ForageGrant:
    """Compute a fresh, ISSUED ForageGrant from `inputs`.

    Args:
        inputs: Every value the computation needs; see `GrantInputs`.

    Returns:
        A new ForageGrant at revision 0, state ISSUED, whose reason records how each figure was
        derived.

    Raises:
        AllocationError: `inputs.ttl_s` is not positive, so no sensible `expires_at` exists.
    """
    # A non-positive ttl would expire the grant at or before the moment it is issued.
    if inputs.ttl_s <= 0:
        raise AllocationError(f"GrantInputs.ttl_s must be positive, got {inputs.ttl_s}.")

    floor = grade_floor(inputs.tempo.accuracy)
    allowed_sources = _sources_clearing_floor(inputs.map, floor, inputs.budgets.spend_cap_usd)
    max_sub_bees = _max_sub_bees(inputs, allowed_sources)
    max_effort = _EFFORT_CEILINGS[inputs.tempo.accuracy]
    usable = 1 - inputs.reserve.headroom_fraction  # The same margin applied to every budget below.

    return ForageGrant(
        id=inputs.grant_id,
        holder=inputs.holder,
        cell_id=inputs.cell_id,
        task_id=inputs.task_id,
        allowed=_allowed_bindings(allowed_sources, max_effort),
        seats=_seat_reservations(allowed_sources, max_sub_bees),
        token_budget=int(inputs.budgets.token_budget * usable),
        spend_budget=inputs.budgets.spend_cap_usd * usable,
        max_sub_bees=max_sub_bees,
        expires_at=inputs.now + timedelta(seconds=inputs.ttl_s),
        reason=_reason(inputs, allowed_sources, max_sub_bees, floor),
        state=GrantState.ISSUED,
    )


def _sources_clearing_floor(
    forage_map: ForageMap, floor: int, spend_cap_usd: float
) -> tuple[ModelSource, ...]:
    """Return every map source whose grade clears `floor` and whose seat-hour cost fits the cap."""
    return tuple(
        source
        for source in forage_map.sources()
        if source.spec.grade >= floor and source.spec.cost.cost_per_seat_hour_usd <= spend_cap_usd
    )


def _max_sub_bees(inputs: GrantInputs, allowed_sources: Sequence[ModelSource]) -> int:
    """Compute the sub-bee ceiling: the tightest of five limits, less the headroom margin."""
    host = inputs.cell_capacity.host
    by_cpu = _bound_by_rate(_free_cores(host), inputs.footprint.cpu_cores)
    memory_after_reserve = max(0, host.memory_free_bytes - inputs.reserve.memory_bytes)
    by_memory = _bound_by_rate(float(memory_after_reserve), float(inputs.footprint.memory_bytes))
    reachable_seats = _reachable_seats(allowed_sources, inputs.reserve.seats)

    # roadmap step 3.12: "the minimum of the Cell's cap, free memory over the footprint's memory,
    # free cores over its cpu, reachable seats, and the goal's remaining bee cap."
    raw = min(
        inputs.cell_capacity.max_sub_bees,
        by_cpu,
        by_memory,
        reachable_seats,
        inputs.budgets.max_sub_bees,
    )
    # Headroom shaves a further margin off whatever the hard limits above allow, absorbing drift
    # between capacity reports (RoyalReserve.headroom_fraction, codingrules section 8.10).
    return int(raw * (1 - inputs.reserve.headroom_fraction))


def _free_cores(host: HostCapacity) -> float:
    """Approximate free logical cores from total cores and the one-minute load average.

    HostCapacity mirrors the wire report and carries load, not a "free cores" figure directly;
    cpu_load is load divided by cores, so the free fraction is (1 - cpu_load), clamped to zero so
    an already-overloaded host (load above 1.0) never turns into a negative core count.
    """
    return max(0.0, host.cores * (1 - host.cpu_load))


def _bound_by_rate(available: float, per_unit: float) -> int:
    """Return how many footprints of size `per_unit` fit in `available`.

    A `per_unit` of zero or less means this dimension costs nothing, so it imposes no ceiling;
    the sentinel is large enough that a real limit elsewhere in the allocator's min() always wins.
    A `per_unit` too small to divide by safely (a subnormal float can push `available / per_unit`
    to `inf`, and `int(inf)` raises `OverflowError`) is treated the same way: practically
    unbounded, not a crash.
    """
    if per_unit <= 0:
        return _UNBOUNDED_SUB_BEES
    raw = available / per_unit
    if not math.isfinite(raw) or raw >= _UNBOUNDED_SUB_BEES:
        return _UNBOUNDED_SUB_BEES
    return int(raw)


def _reachable_seats(sources: Sequence[ModelSource], reserve_seats: int) -> int:
    """Sum free seats across `sources`, capped per-source at what the spec actually offers."""
    total_free = sum(min(source.abundance.seats_free, source.spec.seats) for source in sources)
    return max(0, total_free - reserve_seats)


def _allowed_bindings(
    sources: Sequence[ModelSource], max_effort: Effort
) -> tuple[AllowedBinding, ...]:
    """Build one AllowedBinding per source, all under ModelSlot.WORKER (see module docstring)."""
    return tuple(
        AllowedBinding(slot=ModelSlot.WORKER, source_id=source.source_id, max_effort=max_effort)
        for source in sources
    )


def _seat_reservations(
    sources: Sequence[ModelSource], max_sub_bees: int
) -> tuple[SeatReservation, ...]:
    """Build one SeatReservation per source, never reserving more seats than sub-bees can use."""
    return tuple(
        SeatReservation(
            source_id=source.source_id,
            seats=min(source.abundance.seats_free, max_sub_bees),
            requests_per_minute=source.abundance.requests_per_minute_left,
            tokens_per_minute=source.abundance.tokens_per_minute_left,
        )
        for source in sources
    )


def _reason(
    inputs: GrantInputs, allowed_sources: Sequence[ModelSource], max_sub_bees: int, floor: int
) -> str:
    """Explain how `max_sub_bees` and the allowed sources were derived, for the trail."""
    return (
        f"role={inputs.role.value}: max_sub_bees={max_sub_bees} within cell cap "
        f"{inputs.cell_capacity.max_sub_bees} and goal cap {inputs.budgets.max_sub_bees}, "
        f"{inputs.reserve.headroom_fraction:.0%} headroom applied; {len(allowed_sources)}/"
        f"{len(inputs.map.sources())} map sources cleared grade floor {floor} at tempo "
        f"{inputs.tempo.accuracy.name}."
    )
