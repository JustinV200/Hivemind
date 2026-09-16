"""Tests for hivemind.forage.allocate v1 (roadmap step 4.7): remaining goal caps, tempo, drift.

Split from test_allocate.py (codingrules 5.1/14.2: a test file over 400 lines of code splits by
feature) once step 4.7's own additions pushed that file over the limit; the v0 tests -- ttl,
holder/cell/task carry-through, the cell/memory/seat/headroom ceilings, the grade-floor and
cost-cap source filter, effort-by-tempo -- stay there. `_budgets`/`_inputs`/`_abundant_seats_map`
are duplicated from that module rather than shared, matching how every other split test module in
this repository (`test_queen_liveness.py`, `test_queen_dispatch.py`, ...) keeps its own small
local helpers instead of a cross-file import.

Fits into the Hive:
    Mirrors src/hivemind/forage/allocate.py (codingrules section 3: tests/unit mirrors src/
    one-to-one), for the v1 (roadmap step 4.7) half specifically.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.allocate for the module under test.
    - packages/hivemind/tests/unit/forage/test_allocate.py for the v0 tests and property test.
    - .claude/codingrules.md section 14.3: property-based tests for the pure-core decision
      functions ("a grant never exceeds capacity minus reserve") still hold with v1's new inputs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from builders.forage import (
    make_capacity,
    make_footprint,
    make_host_capacity,
    make_reserve,
    make_source,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.forage.allocate import GoalBudgets, GrantInputs, grant, should_recompute
from hivemind.forage.map import ForageMap
from hivemind.forage.models import (
    Abundance,
    ForageCapacity,
    ForageGrant,
    HostCapacity,
    ModelCost,
    ModelSource,
    RoleFootprint,
    RoyalReserve,
)
from hivemind.forage.tempo import AccuracyBar, Tempo, grade_floor
from waggle.clock import Clock, FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_warden_id
from waggle.messages.forage.values import MAX_MODEL_GRADE, MIN_MODEL_GRADE
from waggle.messages.task import WorkerRole

_GIB = 1024**3


def _budgets(**overrides: object) -> GoalBudgets:
    # See test_allocate.py's own copy of this helper for why the ignore is needed.
    base = GoalBudgets(spend_cap_usd=100.0, token_budget=1_000_000, max_sub_bees=10)
    return replace(base, **overrides)  # type: ignore[arg-type]  # see comment above.


def _inputs(clock: Clock | None = None, **overrides: object) -> GrantInputs:
    active_clock = clock if clock is not None else FakeClock()
    base = GrantInputs(
        cell_capacity=make_capacity(),
        role=WorkerRole.DRONE,
        footprint=make_footprint(),
        tempo=Tempo(),
        map=ForageMap([make_source(source_id="src_1")], clock=active_clock),
        reserve=make_reserve(),
        budgets=_budgets(),
        holder=new_warden_id(active_clock),
        cell_id=new_cell_id(active_clock),
        task_id=None,
        grant_id=new_grant_id(active_clock),
        now=active_clock.now(),
        ttl_s=300.0,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]  # see _budgets' comment above.


def _abundant_seats_map(seats: int = 20) -> ForageMap:
    """Build a one-source map whose seats never bind, so a test can isolate another dimension."""
    source = make_source(
        source_id="src_1",
        seats=seats,
        abundance=Abundance(
            seats_free=seats, requests_per_minute_left=None, tokens_per_minute_left=None
        ),
    )
    return ForageMap([source], clock=FakeClock())


def test_grant_max_sub_bees_is_bound_by_the_goal_s_remaining_bee_cap() -> None:
    inputs = _inputs(
        cell_capacity=make_capacity(max_sub_bees=20),
        budgets=_budgets(max_sub_bees=5),
        goal_sub_bees_used=3,
        footprint=make_footprint(cpu_cores=0.0, memory_bytes=0),
        reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.0),
        map=_abundant_seats_map(),
    )

    result = grant(inputs)

    assert result.max_sub_bees == 2  # 5 - 3 already used by the goal's other live grants.


def test_grant_spend_budget_is_bound_by_the_goal_s_remaining_spend_cap() -> None:
    inputs = _inputs(budgets=_budgets(spend_cap_usd=10.0), goal_spend_used=7.0)

    result = grant(inputs)

    assert result.spend_budget <= 3.0 + 1e-9


def test_grant_never_goes_negative_when_the_goal_is_already_over_its_cap() -> None:
    inputs = _inputs(
        budgets=_budgets(spend_cap_usd=10.0, max_sub_bees=2),
        goal_spend_used=50.0,
        goal_sub_bees_used=10,
    )

    result = grant(inputs)

    assert result.max_sub_bees == 0
    assert result.spend_budget == 0.0


def test_grant_gives_an_urgent_tempo_more_parallelism_than_a_normal_one() -> None:
    # Not a shared dict of overrides (mypy cannot verify a **dict[str, object] never supplies a
    # mistyped `clock`, the same reason _budgets/_inputs above need their own ignore comments):
    # each call spells out every override directly instead.
    normal = grant(
        _inputs(
            tempo=Tempo(),
            cell_capacity=make_capacity(max_sub_bees=20),
            footprint=make_footprint(cpu_cores=0.0, memory_bytes=0),
            reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.4),
            budgets=_budgets(max_sub_bees=20),
            map=_abundant_seats_map(),
        )
    )
    urgent = grant(
        _inputs(
            tempo=Tempo(latency_budget_s=5.0),
            cell_capacity=make_capacity(max_sub_bees=20),
            footprint=make_footprint(cpu_cores=0.0, memory_bytes=0),
            reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.4),
            budgets=_budgets(max_sub_bees=20),
            map=_abundant_seats_map(),
        )
    )

    assert urgent.max_sub_bees > normal.max_sub_bees


def test_grant_gives_a_thorough_tempo_more_spend_than_a_normal_one() -> None:
    normal = grant(
        _inputs(
            tempo=Tempo(accuracy=AccuracyBar.NORMAL),
            reserve=make_reserve(headroom_fraction=0.4),
            budgets=_budgets(spend_cap_usd=10.0),
        )
    )
    thorough = grant(
        _inputs(
            tempo=Tempo(accuracy=AccuracyBar.HIGH),
            reserve=make_reserve(headroom_fraction=0.4),
            budgets=_budgets(spend_cap_usd=10.0),
        )
    )

    assert thorough.spend_budget > normal.spend_budget


def test_grant_excludes_a_source_outside_reachable_source_ids() -> None:
    reachable_source = make_source(source_id="src_reachable", grade=MAX_MODEL_GRADE)
    unreachable_source = make_source(source_id="src_unreachable", grade=MAX_MODEL_GRADE)
    forage_map = ForageMap([reachable_source, unreachable_source], clock=FakeClock())
    inputs = _inputs(map=forage_map, reachable_source_ids=frozenset({"src_reachable"}))

    result = grant(inputs)

    assert {binding.source_id for binding in result.allowed} == {"src_reachable"}


def test_grant_reachable_source_ids_none_keeps_v0_behaviour() -> None:
    source = make_source(source_id="src_ok", grade=MAX_MODEL_GRADE)
    forage_map = ForageMap([source], clock=FakeClock())
    inputs = _inputs(map=forage_map, reachable_source_ids=None)

    result = grant(inputs)

    assert {binding.source_id for binding in result.allowed} == {"src_ok"}


def test_should_recompute_is_false_for_an_unchanged_capacity() -> None:
    capacity = make_capacity()

    assert should_recompute(capacity, capacity, threshold=0.15) is False


def test_should_recompute_is_true_once_free_memory_drifts_past_the_threshold() -> None:
    host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=8 * _GIB)
    previous = make_capacity(host=host)
    drifted_host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=1 * _GIB)
    current = make_capacity(host=drifted_host)

    assert should_recompute(previous, current, threshold=0.15) is True


def test_should_recompute_is_false_within_the_threshold() -> None:
    host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=8 * _GIB)
    previous = make_capacity(host=host)
    nearby_host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=int(7.9 * _GIB))
    current = make_capacity(host=nearby_host)

    assert should_recompute(previous, current, threshold=0.15) is False


def test_should_recompute_is_true_when_a_zero_reading_becomes_non_zero() -> None:
    host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=0)
    previous = make_capacity(host=host)
    grown_host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=1)
    current = make_capacity(host=grown_host)

    assert should_recompute(previous, current, threshold=0.15) is True


# ──────────────────────────────────────────────────────────────────────────────
# Property-based test (codingrules 14.3 and 8.10): a grant never exceeds capacity minus
# reserve, and every allowed binding's grade clears the tempo floor, under v1's new inputs too.
# ──────────────────────────────────────────────────────────────────────────────


@st.composite
def _host_capacities(draw: st.DrawFn) -> HostCapacity:
    memory_bytes = draw(st.integers(min_value=0, max_value=8 * _GIB))
    disk_bytes = draw(st.integers(min_value=0, max_value=8 * _GIB))
    return make_host_capacity(
        cores=draw(st.integers(min_value=1, max_value=32)),
        memory_bytes=memory_bytes,
        memory_free_bytes=draw(st.integers(min_value=0, max_value=memory_bytes)),
        disk_bytes=disk_bytes,
        disk_free_bytes=draw(st.integers(min_value=0, max_value=disk_bytes)),
        cpu_load=draw(
            st.floats(min_value=0.0, max_value=2.0, allow_nan=False, allow_infinity=False)
        ),
    )


@st.composite
def _footprints(draw: st.DrawFn) -> RoleFootprint:
    return make_footprint(
        cpu_cores=draw(
            st.floats(min_value=0.0, max_value=8.0, allow_nan=False, allow_infinity=False)
        ),
        memory_bytes=draw(st.integers(min_value=0, max_value=2 * _GIB)),
    )


@st.composite
def _reserves(draw: st.DrawFn) -> RoyalReserve:
    return make_reserve(
        seats=draw(st.integers(min_value=0, max_value=4)),
        memory_bytes=draw(st.integers(min_value=0, max_value=_GIB)),
        headroom_fraction=draw(
            st.floats(min_value=0.0, max_value=0.9, allow_nan=False, allow_infinity=False)
        ),
    )


@st.composite
def _budget_strategy(draw: st.DrawFn) -> GoalBudgets:
    return _budgets(
        spend_cap_usd=draw(
            st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
        ),
        token_budget=draw(st.integers(min_value=0, max_value=1_000_000)),
        max_sub_bees=draw(st.integers(min_value=0, max_value=16)),
    )


@st.composite
def _source_lists(draw: st.DrawFn) -> tuple[ModelSource, ...]:
    count = draw(st.integers(min_value=0, max_value=4))
    sources: list[ModelSource] = []
    for index in range(count):
        seats = draw(st.integers(min_value=0, max_value=8))
        sources.append(
            make_source(
                source_id=f"src_{index}",
                grade=draw(st.integers(min_value=MIN_MODEL_GRADE, max_value=MAX_MODEL_GRADE)),
                seats=seats,
                cost=ModelCost(
                    cost_per_seat_hour_usd=draw(
                        st.floats(
                            min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False
                        )
                    )
                ),
                abundance=Abundance(
                    seats_free=draw(st.integers(min_value=0, max_value=seats)),
                    requests_per_minute_left=None,
                    tokens_per_minute_left=None,
                ),
            )
        )
    return tuple(sources)


@dataclass(frozen=True, slots=True)
class _GrantScenario:
    """One hypothesis-drawn case for the property test below, bundled into one value.

    codingrules 5.1 caps a function at 5 parameters; `@given` would otherwise need one keyword
    per drawn value, so every draw is bundled into this one dataclass instead.
    """

    host: HostCapacity
    footprint: RoleFootprint
    reserve: RoyalReserve
    budgets: GoalBudgets
    sources: tuple[ModelSource, ...]
    accuracy: AccuracyBar
    cell_cap: int
    goal_sub_bees_used: int  # v1 (roadmap step 4.7): the goal's caps become remaining caps.
    goal_spend_used: float
    latency_budget_s: float | None  # None = no urgency relief; a real value may relieve headroom.
    reachable_all: bool  # True = reachable_source_ids is None (v0 behaviour, every source clears).


@st.composite
def _scenarios(draw: st.DrawFn) -> _GrantScenario:
    """Draw one complete, self-consistent set of grant() inputs, v1 fields included."""
    return _GrantScenario(
        host=draw(_host_capacities()),
        footprint=draw(_footprints()),
        reserve=draw(_reserves()),
        budgets=draw(_budget_strategy()),
        sources=draw(_source_lists()),
        accuracy=draw(st.sampled_from(list(AccuracyBar))),
        cell_cap=draw(st.integers(min_value=0, max_value=16)),
        goal_sub_bees_used=draw(st.integers(min_value=0, max_value=20)),
        goal_spend_used=draw(
            st.floats(min_value=0.0, max_value=120.0, allow_nan=False, allow_infinity=False)
        ),
        latency_budget_s=draw(
            st.none() | st.floats(min_value=0.01, max_value=120.0, allow_nan=False)
        ),
        reachable_all=draw(st.booleans()),
    )


@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_grant_never_exceeds_capacity_minus_reserve_and_clears_the_grade_floor(
    scenario: _GrantScenario,
) -> None:
    clock = FakeClock()
    capacity = ForageCapacity(host=scenario.host, local_seats=(), max_sub_bees=scenario.cell_cap)
    forage_map = ForageMap(scenario.sources, clock=clock)
    # v1: half the drawn cases narrow reachability to the first source only, so the filter is
    # exercised alongside every other dimension; the other half keeps v0's "no narrowing" (None).
    all_ids = tuple(source.source_id for source in scenario.sources)
    reachable = None if scenario.reachable_all or not all_ids else frozenset(all_ids[:1])
    inputs = GrantInputs(
        cell_capacity=capacity,
        role=WorkerRole.DRONE,
        footprint=scenario.footprint,
        tempo=Tempo(accuracy=scenario.accuracy, latency_budget_s=scenario.latency_budget_s),
        map=forage_map,
        reserve=scenario.reserve,
        budgets=scenario.budgets,
        holder=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        task_id=None,
        grant_id=new_grant_id(clock),
        now=clock.now(),
        ttl_s=300.0,
        goal_sub_bees_used=scenario.goal_sub_bees_used,
        goal_spend_used=scenario.goal_spend_used,
        reachable_source_ids=reachable,
    )

    result = grant(inputs)

    _assert_never_exceeds_caps(scenario, result, reachable)


def _assert_never_exceeds_caps(
    scenario: _GrantScenario, result: ForageGrant, reachable: frozenset[str] | None
) -> None:
    """Assert every invariant `test_grant_never_exceeds_...` checks, split out for codingrules 5.1.

    Args:
        scenario: The hypothesis-drawn inputs the grant was computed from.
        result: The `ForageGrant` `grant()` returned.
        reachable: The reachable-source-ids filter that scenario's own inputs used, if narrowed.
    """
    remaining_bees = max(0, scenario.budgets.max_sub_bees - scenario.goal_sub_bees_used)
    remaining_spend = max(0.0, scenario.budgets.spend_cap_usd - scenario.goal_spend_used)
    # Never exceeds the Cell's own cap or the goal's *remaining* cap (v1: the raw budget minus
    # what the goal's other live grants already hold).
    assert 0 <= result.max_sub_bees <= scenario.cell_cap
    assert result.max_sub_bees <= remaining_bees
    # Never exceeds what free memory allows once the Royal Reserve is subtracted.
    if scenario.footprint.memory_bytes > 0:
        memory_after_reserve = max(
            0, scenario.host.memory_free_bytes - scenario.reserve.memory_bytes
        )
        assert result.max_sub_bees * scenario.footprint.memory_bytes <= memory_after_reserve
    # Never exceeds the goal's remaining spend cap, or the token cap (tokens read no tempo, so
    # that half is still bound by the raw budget as in v0).
    assert result.spend_budget <= remaining_spend + 1e-9
    assert result.token_budget <= scenario.budgets.token_budget
    # Every allowed binding clears the tempo's grade floor, is reachable when reachability was
    # narrowed, and never reserves more seats than the source has free.
    floor = grade_floor(scenario.accuracy)
    by_id = {source.source_id: source for source in scenario.sources}
    for binding in result.allowed:
        assert by_id[binding.source_id].spec.grade >= floor
        if reachable is not None:
            assert binding.source_id in reachable
    for reservation in result.seats:
        assert reservation.seats <= by_id[reservation.source_id].abundance.seats_free
