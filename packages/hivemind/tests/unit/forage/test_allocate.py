"""Tests for hivemind.forage.allocate: GoalBudgets, GrantInputs and grant().

Fits into the Hive:
    Mirrors src/hivemind/forage/allocate.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.allocate for the module under test.
    - .claude/codingrules.md section 14.3: property-based tests for the pure-core decision
      functions ("a grant never exceeds capacity minus reserve").
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from builders.forage import (
    make_capacity,
    make_footprint,
    make_host_capacity,
    make_reserve,
    make_source,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from hivemind.forage.allocate import GoalBudgets, GrantInputs, grant
from hivemind.forage.errors import AllocationError
from hivemind.forage.grant_state import GrantState
from hivemind.forage.map import ForageMap
from hivemind.forage.models import (
    Abundance,
    ForageCapacity,
    HostCapacity,
    ModelCost,
    ModelSource,
    RoleFootprint,
    RoyalReserve,
)
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo, grade_floor
from waggle.clock import Clock, FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_task_id, new_warden_id
from waggle.messages.forage.values import MAX_MODEL_GRADE, MIN_MODEL_GRADE
from waggle.messages.task import WorkerRole

_GIB = 1024**3


def _budgets(**overrides: object) -> GoalBudgets:
    # GoalBudgets and GrantInputs are plain dataclasses (allocate.py's own docstring: grant() stays
    # pure, so its inputs are grouping values, not pydantic boundary models); unlike a pydantic
    # model, `dataclasses.replace`'s generated stub checks each override against that field's own
    # concrete type, so a **dict[str, object] of overrides never type-checks even when it is
    # empty. The ignore is scoped to this one call, not the whole function, and only fires because
    # mypy cannot see that a test caller supplies the right type per named override.
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


def test_grant_rejects_a_non_positive_ttl() -> None:
    with pytest.raises(AllocationError, match="ttl_s"):
        grant(_inputs(ttl_s=0.0))


def test_grant_returns_a_fresh_grant_at_revision_zero_and_issued() -> None:
    result = grant(_inputs())

    assert result.state is GrantState.ISSUED
    assert result.revision == 0
    assert result.tokens_spent == 0
    assert result.spent == 0.0


def test_grant_carries_the_holder_cell_task_and_id_from_inputs() -> None:
    inputs = _inputs(task_id=new_task_id(FakeClock()))

    result = grant(inputs)

    assert result.id == inputs.grant_id
    assert result.holder == inputs.holder
    assert result.cell_id == inputs.cell_id
    assert result.task_id == inputs.task_id


def test_grant_sets_expires_at_from_now_plus_ttl() -> None:
    clock = FakeClock()
    inputs = _inputs(clock=clock, now=clock.now(), ttl_s=60.0)

    result = grant(inputs)

    assert (result.expires_at - inputs.now).total_seconds() == 60.0


def test_grant_max_sub_bees_is_bound_by_the_cell_cap() -> None:
    inputs = _inputs(
        cell_capacity=make_capacity(max_sub_bees=1),
        budgets=_budgets(max_sub_bees=100),
        footprint=make_footprint(cpu_cores=0.0, memory_bytes=0),
        reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.0),
    )

    result = grant(inputs)

    assert result.max_sub_bees == 1


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


def test_grant_max_sub_bees_is_bound_by_free_memory_over_footprint_memory() -> None:
    host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=3 * _GIB)
    inputs = _inputs(
        cell_capacity=make_capacity(host=host, max_sub_bees=20),
        footprint=make_footprint(cpu_cores=0.0, memory_bytes=1 * _GIB),
        reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.0),
        budgets=_budgets(max_sub_bees=20),
        map=_abundant_seats_map(),
    )

    result = grant(inputs)

    assert result.max_sub_bees == 3  # 3 GiB free // 1 GiB per bee.


def test_grant_subtracts_the_royal_reserve_memory_before_dividing() -> None:
    host = make_host_capacity(memory_bytes=10 * _GIB, memory_free_bytes=3 * _GIB)
    inputs = _inputs(
        cell_capacity=make_capacity(host=host, max_sub_bees=20),
        footprint=make_footprint(cpu_cores=0.0, memory_bytes=1 * _GIB),
        reserve=make_reserve(seats=0, memory_bytes=1 * _GIB, headroom_fraction=0.0),
        budgets=_budgets(max_sub_bees=20),
        map=_abundant_seats_map(),
    )

    result = grant(inputs)

    assert result.max_sub_bees == 2  # (3 - 1) GiB free after reserve // 1 GiB per bee.


def test_grant_headroom_fraction_shaves_a_margin_off_the_raw_limit() -> None:
    inputs = _inputs(
        cell_capacity=make_capacity(max_sub_bees=10),
        footprint=make_footprint(cpu_cores=0.0, memory_bytes=0),
        reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.5),
        budgets=_budgets(max_sub_bees=20),
        map=_abundant_seats_map(),
    )

    result = grant(inputs)

    assert result.max_sub_bees == 5  # 10 * (1 - 0.5).


def test_grant_excludes_a_source_below_the_grade_floor() -> None:
    forage_map = ForageMap(
        [make_source(source_id="src_low", grade=MIN_MODEL_GRADE)], clock=FakeClock()
    )
    inputs = _inputs(map=forage_map, tempo=Tempo(accuracy=AccuracyBar.CRITICAL))

    result = grant(inputs)

    assert result.allowed == ()
    assert result.seats == ()


def test_grant_excludes_a_source_whose_cost_exceeds_the_spend_cap() -> None:
    expensive = make_source(source_id="src_expensive", cost=ModelCost(cost_per_seat_hour_usd=50.0))
    forage_map = ForageMap([expensive], clock=FakeClock())
    inputs = _inputs(map=forage_map, budgets=_budgets(spend_cap_usd=1.0))

    result = grant(inputs)

    assert result.allowed == ()


def test_grant_includes_a_source_that_clears_both_the_floor_and_the_cost_cap() -> None:
    source = make_source(source_id="src_ok", grade=MAX_MODEL_GRADE, cost=ModelCost())
    forage_map = ForageMap([source], clock=FakeClock())
    inputs = _inputs(map=forage_map)

    result = grant(inputs)

    assert len(result.allowed) == 1
    assert result.allowed[0].source_id == "src_ok"
    assert result.allowed[0].slot is ModelSlot.WORKER


def test_grant_seat_reservation_never_exceeds_the_sources_free_seats() -> None:
    # spec.seats must also cover 2 (_reachable_seats caps per-source at min(seats_free,
    # spec.seats)), so both figures need to be 2 for the sub-bee ceiling to actually reach 2.
    source = make_source(
        source_id="src_1",
        seats=2,
        abundance=Abundance(
            seats_free=2, requests_per_minute_left=None, tokens_per_minute_left=None
        ),
    )
    forage_map = ForageMap([source], clock=FakeClock())
    inputs = _inputs(
        map=forage_map,
        cell_capacity=make_capacity(max_sub_bees=20),
        budgets=_budgets(max_sub_bees=20),
        footprint=make_footprint(cpu_cores=0.0, memory_bytes=0),
        reserve=make_reserve(seats=0, memory_bytes=0, headroom_fraction=0.0),
    )

    result = grant(inputs)

    assert result.seats[0].seats == 2


def test_grant_reason_names_the_role() -> None:
    result = grant(_inputs(role=WorkerRole.FORAGER))

    assert "FORAGER" in result.reason


@pytest.mark.parametrize(
    ("accuracy", "expected_effort"),
    [
        (AccuracyBar.LOW, Effort.LOW),
        (AccuracyBar.NORMAL, Effort.MEDIUM),
        (AccuracyBar.HIGH, Effort.HIGH),
        (AccuracyBar.CRITICAL, Effort.HIGH),
    ],
)
def test_grant_max_effort_follows_the_tempo_accuracy_bar(
    accuracy: AccuracyBar, expected_effort: Effort
) -> None:
    source = make_source(source_id="src_ok", grade=MAX_MODEL_GRADE)
    forage_map = ForageMap([source], clock=FakeClock())
    inputs = _inputs(map=forage_map, tempo=Tempo(accuracy=accuracy))

    result = grant(inputs)

    assert result.allowed[0].max_effort is expected_effort


# ──────────────────────────────────────────────────────────────────────────────
# Property-based tests (codingrules 14.3 and 8.10): a grant never exceeds capacity minus
# reserve, and every allowed binding's grade clears the tempo floor.
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


@st.composite
def _scenarios(draw: st.DrawFn) -> _GrantScenario:
    """Draw one complete, self-consistent set of grant() inputs."""
    return _GrantScenario(
        host=draw(_host_capacities()),
        footprint=draw(_footprints()),
        reserve=draw(_reserves()),
        budgets=draw(_budget_strategy()),
        sources=draw(_source_lists()),
        accuracy=draw(st.sampled_from(list(AccuracyBar))),
        cell_cap=draw(st.integers(min_value=0, max_value=16)),
    )


@settings(max_examples=100, deadline=None)
@given(scenario=_scenarios())
def test_grant_never_exceeds_capacity_minus_reserve_and_clears_the_grade_floor(
    scenario: _GrantScenario,
) -> None:
    clock = FakeClock()
    capacity = ForageCapacity(host=scenario.host, local_seats=(), max_sub_bees=scenario.cell_cap)
    forage_map = ForageMap(scenario.sources, clock=clock)
    inputs = GrantInputs(
        cell_capacity=capacity,
        role=WorkerRole.DRONE,
        footprint=scenario.footprint,
        tempo=Tempo(accuracy=scenario.accuracy),
        map=forage_map,
        reserve=scenario.reserve,
        budgets=scenario.budgets,
        holder=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        task_id=None,
        grant_id=new_grant_id(clock),
        now=clock.now(),
        ttl_s=300.0,
    )

    result = grant(inputs)

    # Never exceeds the Cell's own cap or the goal's cap.
    assert 0 <= result.max_sub_bees <= scenario.cell_cap
    assert result.max_sub_bees <= scenario.budgets.max_sub_bees
    # Never exceeds what free memory allows once the Royal Reserve is subtracted.
    if scenario.footprint.memory_bytes > 0:
        memory_after_reserve = max(
            0, scenario.host.memory_free_bytes - scenario.reserve.memory_bytes
        )
        assert result.max_sub_bees * scenario.footprint.memory_bytes <= memory_after_reserve
    # Never exceeds the budgets' spend and token caps.
    assert result.spend_budget <= scenario.budgets.spend_cap_usd + 1e-9
    assert result.token_budget <= scenario.budgets.token_budget
    # Every allowed binding clears the tempo's grade floor, and never reserves more seats than
    # the source has free.
    floor = grade_floor(scenario.accuracy)
    by_id = {source.source_id: source for source in scenario.sources}
    for binding in result.allowed:
        assert by_id[binding.source_id].spec.grade >= floor
    for reservation in result.seats:
        assert reservation.seats <= by_id[reservation.source_id].abundance.seats_free
