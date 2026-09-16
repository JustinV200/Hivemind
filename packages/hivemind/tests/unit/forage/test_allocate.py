"""Tests for hivemind.forage.allocate v0: GoalBudgets, GrantInputs and grant().

v1's own tests (roadmap step 4.7: remaining goal caps, tempo headroom relief, reachability,
drift recomputation, and the property test) live in test_allocate_v1.py -- split out once this
module's own line count passed codingrules 5.1's test-file limit; see that module's docstring.

Fits into the Hive:
    Mirrors src/hivemind/forage/allocate.py (codingrules section 3: tests/unit mirrors src/
    one-to-one), for the v0 half specifically.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.allocate for the module under test.
    - packages/hivemind/tests/unit/forage/test_allocate_v1.py for v1's own tests.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from builders.forage import (
    make_capacity,
    make_footprint,
    make_host_capacity,
    make_reserve,
    make_source,
)

from hivemind.forage.allocate import GoalBudgets, GrantInputs, grant
from hivemind.forage.errors import AllocationError
from hivemind.forage.grant_state import GrantState
from hivemind.forage.map import ForageMap
from hivemind.forage.models import Abundance, ModelCost
from hivemind.forage.slots import Effort, ModelSlot
from hivemind.forage.tempo import AccuracyBar, Tempo
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
