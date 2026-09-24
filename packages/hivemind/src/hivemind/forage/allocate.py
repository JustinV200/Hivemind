"""Define GrantInputs and grant(): the pure Forage allocator, v0 plus roadmap step 4.7's v1.

`grant` is pure in the sense codingrules section 8.3 asks of a decision function: plain data in,
a plain `ForageGrant` out, no I/O, and the same inputs always produce the same grant. It computes
`max_sub_bees` as the minimum of the Cell's own cap, free memory over the role's footprint memory,
free cores over its cpu, reachable model seats, and the goal's remaining bee cap (roadmap step
3.12); `allowed` bindings are the Forage map sources whose grade clears the task's tempo floor
(`hivemind.forage.tempo.grade_floor`), whose cost fits the spend budget and, when
`GrantInputs.reachable_source_ids` narrows the map, whose id is in that set (roadmap step 4.7:
"reachability under the Cell's network policy and hosting decision" -- computed by the caller, a
Layer-2-and-above concern this Layer-1 module may not read for itself, and handed in as plain ids).
The Royal Reserve is subtracted before any of that, and a further headroom margin shaves a
percentage off whatever remains, so a grant never runs right up to the edge of what a capacity
report might be slightly stale about; v1 narrows that margin for an urgent task's parallelism and
widens the usable share of spend for a thorough one (roadmap step 4.7: "urgent work may get more
parallelism, thorough work more spend"), while never letting either exceed the hard ceilings below
-- the property test that a grant never exceeds capacity minus reserve holds under every tempo.
`GrantInputs` groups every input into one frozen dataclass (codingrules section 8.2's
four-collaborator rule and section 5.1's five-parameter limit both push a function with this many
inputs toward one grouping value rather than a long parameter list); v1 adds `goal_spend_used` and
`goal_sub_bees_used`, so `GoalBudgets`' own per-goal ceilings become genuinely *remaining* caps
(spend and bees the goal has already drawn, tracked by the Queen's ledger,
`hivemind.queen.forage.ledger`, are subtracted here rather than by every caller). `should_recompute`
is v1's other addition: a pure comparison of two `ForageCapacity` readings against the manifest's
`[forage] measurement_drift_threshold`, so the ledger knows when a live grant should be recomputed
rather than reused. `GoalBudgets` is the small slice of the manifest's `[forage]` section (spend
cap, token budget, sub-bee cap per goal) `grant` needs, defined here rather than imported from
`hivemind.manifest` because Layer 1 may not import Layer 2 (codingrules section 4).

The five limits are data as well as a number (the zero-grant fix): `sub_bee_limits` returns a
`SubBeeLimits` naming each limit (`GrantBound`) with its value as the figures stand and its value
at their best (an idle host, all of its memory free, every seat free, none of the goal's allowance
held elsewhere), so the Queen's dispatcher can tell a grant that a passing shortfall zeroed (the
Hive Stand's load right now) from one no wait can ever lift (a reserve that claims every seat)
without parsing `ForageGrant.reason`. `grant` reads the very same computation, so the two can never
disagree, and its reason now names the tightest limit too. `goal_limit` is the goal's own allowance
alone, for a caller that must know whether a goal has room before any Cell is chosen.

Fits into the Hive:
    Layer 1 (forage; foundational services, capacity as data). Called by the Queen's dispatcher
    (`hivemind.queen.dispatcher`) and by `hivemind.queen.forage.requests` when it answers a
    `ForageRequest`. Calls into `hivemind.forage.errors`, `hivemind.forage.grant_state`,
    `hivemind.forage.map`, `hivemind.forage.models`, `hivemind.forage.slots`,
    `hivemind.forage.tempo` and `waggle.messages.task` (`WorkerRole`) only.

Key invariants:
    - grant() never mutates `inputs.map`; it only reads `ForageMap.sources()`.
    - The returned ForageGrant's max_sub_bees never exceeds `inputs.cell_capacity.max_sub_bees`,
      `inputs.budgets.max_sub_bees - inputs.goal_sub_bees_used`, or what the Cell's free memory
      allows once `inputs.reserve.memory_bytes` is subtracted (property-tested with hypothesis),
      under every Tempo v1 can draw.
    - Every AllowedBinding grant() returns names a source whose grade is at least
      `grade_floor(inputs.tempo.accuracy)` and, when `inputs.reachable_source_ids` is not None,
      whose id is a member of it.
    - `grant(inputs).max_sub_bees == sub_bee_limits(inputs).max_sub_bees` for every input: both
      read one computation (`_limits`), so a caller's reading of why a grant is zero is always
      about the grant it actually got.
    - Every value in `SubBeeLimits.at_best` is at least its value in `.now`: a figure at its best
      never allows fewer bees than it does as it stands.
    - v0 names every allowed binding under `ModelSlot.WORKER`; per-role slot mapping and per-slot
      hosting plans (`hivemind.forage.models.pools.HostingPlan`) are the Queen's job, a later
      phase this allocator does not attempt.

See Also:
    - .claude/roadmap.md step 3.12 for the v0 allocator's exact formula and step 4.7 for v1's.
    - .claude/codingrules.md section 8.10 for grants, the Royal Reserve and the two pools.
    - .claude/codingrules.md section 8.14 for tempo's role in Forage allocation.
    - hivemind.forage.tempo for grade_floor, the grade-floor-by-accuracy-bar table.
    - hivemind.forage.map for ForageMap, one of GrantInputs' fields.
    - hivemind.forage.grant_state for GrantState, the state every fresh grant starts in.
    - hivemind.queen.forage.ledger for the live book that supplies goal_spend_used,
      goal_sub_bees_used and reachable_source_ids, and that calls should_recompute.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

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

# v1 (roadmap step 4.7): a task whose latency budget is this tight or tighter counts as "urgent"
# for parallelism purposes; chosen well below the manifest's own default grant_ttl_s (300s) so a
# task that merely wants to finish before the grant would expire anyway does not count.
_URGENT_LATENCY_THRESHOLD_S = 30.0
# Urgent work keeps only half the usual headroom margin on max_sub_bees, trading a little of the
# safety buffer for parallelism; never the whole margin, so a burst of urgent grants still leaves
# some slack against measurement drift.
_URGENT_HEADROOM_RELIEF_FACTOR = 0.5
# HIGH and CRITICAL accuracy bars are "thorough" for spend purposes (the same two bars already get
# Effort.HIGH above); thorough work keeps a smaller spend margin, so more of what remains of the
# goal's own cap is actually usable, never more than the cap itself.
_THOROUGH_ACCURACY_BARS = frozenset({AccuracyBar.HIGH, AccuracyBar.CRITICAL})
_THOROUGH_HEADROOM_RELIEF_FACTOR = 0.5

__all__ = [
    "GoalBudgets",
    "GrantBound",
    "GrantInputs",
    "SubBeeLimits",
    "goal_limit",
    "grant",
    "should_recompute",
    "sub_bee_limits",
]


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
        goal_sub_bees_used: Sub-bees the goal's other live grants already account for (roadmap
            step 4.7); subtracted from `budgets.max_sub_bees` so the cap this computation applies
            is what remains, not the goal's whole allowance. Defaults to 0 (v0's own behaviour:
            the whole allowance is still available).
        goal_spend_used: Spend the goal's other live grants already account for; subtracted from
            `budgets.spend_cap_usd` the same way. Defaults to 0.0.
        reachable_source_ids: Forage map source ids reachable under the Cell's network policy and
            hosting decision (a Layer-2-and-above computation the caller supplies, since this
            Layer-1 module may not read `hivemind.cell` or `hivemind.forage.models.pools.
            HostingPlan` for itself); None means every source the grade floor and cost already
            allow is reachable, v0's own behaviour.
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
    goal_sub_bees_used: int = 0
    goal_spend_used: float = 0.0
    reachable_source_ids: frozenset[str] | None = None


class GrantBound(Enum):
    """Name one of the five limits a grant's `max_sub_bees` is the minimum of (roadmap 3.12).

    Carried as data (`SubBeeLimits`) so a caller can tell which limit left a grant with no bee
    without parsing `ForageGrant.reason`; the value is the label the Pheromone Trail carries.
    Declaration order breaks ties wherever one limit has to be named for several.
    """

    CELL_CAP = "cell_cap"  # The Cell's own reported max_sub_bees.
    FREE_CORES = "free_cores"  # Cores less the one-minute load, over the footprint's cpu.
    FREE_MEMORY = "free_memory"  # Free memory less the Royal Reserve, over the footprint's.
    SEATS = "seats"  # Free seats on every allowed source, less the Royal Reserve's seats.
    GOAL_BEES = "goal_bees"  # What is left of the goal's own sub-bee allowance.


@dataclass(frozen=True, slots=True)
class SubBeeLimits:
    """Each limit on a grant's sub-bees as the figures stand now, and at their best.

    A limit that leaves no whole bee even at its best never lifts by waiting (a reserve that
    claims every seat, a Cell whose whole memory cannot hold one footprint); one that leaves a bee
    at its best but not now is a passing shortfall, which a caller may choose to wait out when it
    knows the figure behind it is refreshed.

    Attributes:
        now: Each limit from the figures as given, in `GrantBound` order: the values `grant`
            takes the minimum of.
        at_best: Each limit with every figure that moves on its own at its best: an idle host
            (no load, all of its memory free), every seat on the allowed sources free, and none
            of the goal's allowance held by its other work. Never below the matching `now`.
        margin: The share of the tightest limit a grant keeps once the headroom margin is taken
            (0.9 at the default 10% margin; urgent work keeps more).
    """

    now: Mapping[GrantBound, int]
    at_best: Mapping[GrantBound, int]
    margin: float

    @property
    def max_sub_bees(self) -> int:
        """Return the sub-bee ceiling these limits allow: the tightest now, less the margin."""
        return _whole_bees(min(self.now.values()), self.margin)

    @property
    def limited_by(self) -> GrantBound:
        """Return the tightest limit now; the first in `GrantBound` order among equals."""
        # min() keeps the first of equal keys, and `now` is built in GrantBound order.
        return min(self.now, key=lambda bound: self.now[bound])

    @property
    def lasting(self) -> GrantBound | None:
        """Return the first limit, in `GrantBound` order, that no wait lifts; None if none."""
        stuck = self.never_lifts()
        return next((bound for bound in GrantBound if bound in stuck), None)

    def short(self) -> frozenset[GrantBound]:
        """Return every limit that on its own leaves no whole bee as the figures stand now."""
        return _short_of_one(self.now, self.margin)

    def never_lifts(self) -> frozenset[GrantBound]:
        """Return every limit that leaves no whole bee even with every figure at its best."""
        return _short_of_one(self.at_best, self.margin)


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

    # v1: the goal's spend cap becomes a *remaining* cap once what its other live grants already
    # spent is subtracted (roadmap step 4.7); clamped at zero so a goal already past its cap never
    # goes negative. The bee cap gets the same treatment inside _limits.
    remaining_spend = max(0.0, inputs.budgets.spend_cap_usd - inputs.goal_spend_used)

    floor = grade_floor(inputs.tempo.accuracy)
    allowed_sources = _sources_clearing_floor(inputs, floor, remaining_spend)
    limits = _limits(inputs, allowed_sources)
    max_sub_bees = limits.max_sub_bees
    max_effort = _EFFORT_CEILINGS[inputs.tempo.accuracy]
    # v1: a thorough task (HIGH/CRITICAL accuracy) keeps a smaller spend margin, so more of what
    # remains of the goal's own cap is actually usable; every other bar keeps v0's full margin.
    # Either way `spend_usable <= 1`, so spend_budget can never exceed remaining_spend itself.
    thorough_factor = _THOROUGH_HEADROOM_RELIEF_FACTOR if _is_thorough(inputs) else 1.0
    spend_usable = 1 - inputs.reserve.headroom_fraction * thorough_factor
    token_usable = 1 - inputs.reserve.headroom_fraction  # Unchanged from v0: tokens read no tempo.

    return ForageGrant(
        id=inputs.grant_id,
        holder=inputs.holder,
        cell_id=inputs.cell_id,
        task_id=inputs.task_id,
        allowed=_allowed_bindings(allowed_sources, max_effort),
        seats=_seat_reservations(allowed_sources, max_sub_bees),
        token_budget=int(inputs.budgets.token_budget * token_usable),
        spend_budget=remaining_spend * spend_usable,
        max_sub_bees=max_sub_bees,
        expires_at=inputs.now + timedelta(seconds=inputs.ttl_s),
        reason=_reason(inputs, allowed_sources, limits, floor),
        state=GrantState.ISSUED,
    )


def sub_bee_limits(inputs: GrantInputs) -> SubBeeLimits:
    """Return each of the five limits `grant(inputs)` sizes `max_sub_bees` from, now and at best.

    Args:
        inputs: The same inputs `grant` reads; see `GrantInputs`.

    Returns:
        The limits, whose `max_sub_bees` always equals `grant(inputs).max_sub_bees`.

    Example:
        >>> limits = sub_bee_limits(inputs)  # doctest: +SKIP
        >>> limits.limited_by, limits.never_lifts()  # doctest: +SKIP
        (<GrantBound.FREE_CORES: 'free_cores'>, frozenset())
    """
    # The same allowed sources `grant` draws its seats from, so SEATS is read off the same set.
    remaining_spend = max(0.0, inputs.budgets.spend_cap_usd - inputs.goal_spend_used)
    floor = grade_floor(inputs.tempo.accuracy)
    return _limits(inputs, _sources_clearing_floor(inputs, floor, remaining_spend))


def goal_limit(
    budgets: GoalBudgets, sub_bees_used: int, reserve: RoyalReserve, tempo: Tempo
) -> SubBeeLimits:
    """Return a goal's own sub-bee allowance alone, as limits with only `GOAL_BEES` in them.

    For a caller that must know whether a goal has room for one more bee before any Cell (and so
    any `GrantInputs`) exists: the same arithmetic `grant` applies to `GOAL_BEES`, the same margin.

    Args:
        budgets: The goal's caps; `max_sub_bees` is its whole allowance.
        sub_bees_used: Sub-bees the goal's other work already holds; clamped so the allowance
            left is never negative.
        reserve: The Royal Reserve whose headroom margin applies.
        tempo: The task's tempo; an urgent one keeps more of the margin, exactly as in `grant`.

    Returns:
        Limits whose `now` is what is left of the allowance and whose `at_best` is all of it.
    """
    left = max(0, budgets.max_sub_bees - sub_bees_used)
    return SubBeeLimits(
        now={GrantBound.GOAL_BEES: left},
        at_best={GrantBound.GOAL_BEES: max(budgets.max_sub_bees, left)},
        margin=_margin(reserve, tempo),
    )


def should_recompute(previous: ForageCapacity, current: ForageCapacity, threshold: float) -> bool:
    """Return whether `current` has drifted from `previous` enough to recompute a live grant.

    Roadmap step 4.7: "recomputation when measurements drift past a manifest threshold"
    (`hivemind.manifest.schema.forage.ForageSection.measurement_drift_threshold`). Compares the
    two dimensions `_max_sub_bees` actually reads off `ForageCapacity.host`: free memory and the
    free-cores figure `_free_cores` derives from cpu_load. A dimension whose previous reading was
    zero counts as drifted the moment the new reading is not also zero, since a relative change is
    undefined at zero and "went from nothing to something" is itself worth a fresh computation.

    Args:
        previous: The capacity a live grant was last computed from.
        current: The Cell's latest reported capacity.
        threshold: The fraction a dimension must move by, relative to its previous reading, to
            count as drifted (`ForageSection.measurement_drift_threshold`).

    Returns:
        True if either dimension moved by more than `threshold` (or from zero to non-zero).
    """
    return _drifted(
        previous.host.memory_free_bytes, current.host.memory_free_bytes, threshold
    ) or _drifted(_free_cores(previous.host), _free_cores(current.host), threshold)


def _drifted(previous: float, current: float, threshold: float) -> bool:
    """Return whether `current` moved from `previous` by more than `threshold`, relatively."""
    if previous == 0:
        return current != 0
    return abs(current - previous) / previous > threshold


def _sources_clearing_floor(
    inputs: GrantInputs, floor: int, remaining_spend: float
) -> tuple[ModelSource, ...]:
    """Return every map source clearing the grade floor, the spend cap and reachability."""
    reachable = inputs.reachable_source_ids
    return tuple(
        source
        for source in inputs.map.sources()
        if source.spec.grade >= floor
        and source.spec.cost.cost_per_seat_hour_usd <= remaining_spend
        # None means "no narrowing": every source the two checks above already allow is reachable
        # (v0's own behaviour, unchanged when a caller never supplies inputs.reachable_source_ids).
        and (reachable is None or source.source_id in reachable)
    )


def _limits(inputs: GrantInputs, allowed_sources: Sequence[ModelSource]) -> SubBeeLimits:
    """Compute the five sub-bee limits, as they stand and at their best, and the margin."""
    host = inputs.cell_capacity.host
    cap = inputs.cell_capacity.max_sub_bees
    # v1: the goal's own bee cap is what is left of it (roadmap step 4.7), clamped at zero so a
    # goal already at or past its cap never goes negative and turns the min() into a false
    # "unbounded" reading.
    goal_left = max(0, inputs.budgets.max_sub_bees - inputs.goal_sub_bees_used)
    # roadmap step 3.12: "the minimum of the Cell's cap, free memory over the footprint's memory,
    # free cores over its cpu, reachable seats, and the goal's remaining bee cap", as they stand.
    now = {
        GrantBound.CELL_CAP: cap,
        GrantBound.FREE_CORES: _bound_by_rate(_free_cores(host), inputs.footprint.cpu_cores),
        GrantBound.FREE_MEMORY: _bound_by_memory(host.memory_free_bytes, inputs),
        GrantBound.SEATS: _reachable_seats(allowed_sources, inputs.reserve.seats),
        GrantBound.GOAL_BEES: goal_left,
    }
    # At best: no load on any core, all of the host's memory free, every seat on the same allowed
    # sources free, and none of the goal's allowance held; the Cell's own cap never moves.
    at_best = {
        GrantBound.CELL_CAP: cap,
        GrantBound.FREE_CORES: _bound_by_rate(float(host.cores), inputs.footprint.cpu_cores),
        GrantBound.FREE_MEMORY: _bound_by_memory(host.memory_bytes, inputs),
        GrantBound.SEATS: _reachable_seats_at_best(allowed_sources, inputs.reserve.seats),
        GrantBound.GOAL_BEES: max(inputs.budgets.max_sub_bees, goal_left),
    }
    return SubBeeLimits(now=now, at_best=at_best, margin=_margin(inputs.reserve, inputs.tempo))


def _margin(reserve: RoyalReserve, tempo: Tempo) -> float:
    """Return the share of the tightest limit a grant keeps once the headroom margin is taken.

    v1: an urgent task (a tight latency budget) keeps only half the usual headroom margin,
    trading some safety buffer for parallelism; every other task keeps v0's full margin. Either
    way the margin is never negative, so a grant can never exceed its tightest limit.
    """
    urgent_factor = _URGENT_HEADROOM_RELIEF_FACTOR if _is_urgent(tempo) else 1.0
    return 1 - reserve.headroom_fraction * urgent_factor


def _whole_bees(limit: int, margin: float) -> int:
    """Return how many whole sub-bees `limit` allows once `margin` is taken."""
    return int(limit * margin)


def _short_of_one(limits: Mapping[GrantBound, int], margin: float) -> frozenset[GrantBound]:
    """Return every limit in `limits` that leaves no whole sub-bee once `margin` is taken."""
    return frozenset(bound for bound, value in limits.items() if _whole_bees(value, margin) < 1)


def _bound_by_memory(free_bytes: int, inputs: GrantInputs) -> int:
    """Return how many footprints fit in `free_bytes` once the Royal Reserve's memory is held."""
    after_reserve = max(0, free_bytes - inputs.reserve.memory_bytes)
    return _bound_by_rate(float(after_reserve), float(inputs.footprint.memory_bytes))


def _is_urgent(tempo: Tempo) -> bool:
    """Return whether `tempo` counts as urgent for parallelism relief (module docstring)."""
    budget = tempo.latency_budget_s
    return budget is not None and budget <= _URGENT_LATENCY_THRESHOLD_S


def _is_thorough(inputs: GrantInputs) -> bool:
    """Return whether `inputs.tempo.accuracy` counts as thorough for spend relief."""
    return inputs.tempo.accuracy in _THOROUGH_ACCURACY_BARS


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


def _reachable_seats_at_best(sources: Sequence[ModelSource], reserve_seats: int) -> int:
    """Sum every seat `sources` offer, all free, less the reserve's: the most seats can allow."""
    return max(0, sum(source.spec.seats for source in sources) - reserve_seats)


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
    inputs: GrantInputs, allowed_sources: Sequence[ModelSource], limits: SubBeeLimits, floor: int
) -> str:
    """Explain how `max_sub_bees` and the allowed sources were derived, for the trail."""
    return (
        f"role={inputs.role.value}: max_sub_bees={limits.max_sub_bees} within cell cap "
        f"{inputs.cell_capacity.max_sub_bees} and goal cap {inputs.budgets.max_sub_bees}, "
        f"{inputs.reserve.headroom_fraction:.0%} headroom applied, limited by "
        f"{limits.limited_by.value}; {len(allowed_sources)}/{len(inputs.map.sources())} map "
        f"sources cleared grade floor {floor} at tempo {inputs.tempo.accuracy.name}."
    )
