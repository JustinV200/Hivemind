"""Tests for hivemind.forage.allocate's limits as data: GrantBound, SubBeeLimits, goal_limit.

The zero-grant fix: the allocator names which of its five limits left a grant with no bee, and
whether that limit could ever lift, so the Queen's dispatcher can let a task wait out a passing
shortfall (the Hive Stand's load right now) and still deny a lasting one at once.

Fits into the Hive:
    Mirrors src/hivemind/forage/allocate.py (codingrules section 3), split by feature (14.2) from
    test_allocate.py (v0) and test_allocate_v1.py (v1).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.forage.allocate for the module under test.
    - hivemind.queen.dispatcher.zero_grant for the dispatcher rule these limits feed.
"""

from __future__ import annotations

from dataclasses import replace

from builders.forage import (
    make_capacity,
    make_footprint,
    make_host_capacity,
    make_reserve,
    make_source,
)
from hypothesis import given
from hypothesis import strategies as st

from hivemind.forage import (
    GoalBudgets,
    GrantBound,
    GrantInputs,
    RoyalReserve,
    Tempo,
    goal_limit,
    grant,
    sub_bee_limits,
)
from hivemind.forage.map import ForageMap
from hivemind.forage.models import Abundance
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_grant_id, new_warden_id
from waggle.messages.task import WorkerRole

_GIB = 1024**3
_MIB = 1024**2
_SEATS = 4  # Every source here offers four seats, so the default reserve of one leaves three.
_BUDGETS = GoalBudgets(spend_cap_usd=100.0, token_budget=1_000_000, max_sub_bees=4)


def _inputs(**overrides: object) -> GrantInputs:
    """Build GrantInputs over an idle 8-core host, a 1-core footprint and a four-seat source."""
    clock = FakeClock()
    source = make_source(source_id="src_1", seats=_SEATS)
    base = GrantInputs(
        cell_capacity=make_capacity(),
        role=WorkerRole.DRONE,
        footprint=make_footprint(),
        tempo=Tempo(),
        map=ForageMap([source], clock=clock),
        reserve=make_reserve(),
        budgets=_BUDGETS,
        holder=new_warden_id(clock),
        cell_id=new_cell_id(clock),
        task_id=None,
        grant_id=new_grant_id(clock),
        now=clock.now(),
        ttl_s=300.0,
    )
    # A plain dataclass: `replace`'s stub checks each override's own type, which a **dict of
    # overrides cannot show mypy (test_allocate.py's `_budgets` explains the same ignore).
    return replace(base, **overrides)  # type: ignore[arg-type]


def test_a_host_loaded_to_its_cores_is_limited_by_free_cores_which_lift_at_best() -> None:
    # 8 cores at a load of 7.9 leave 0.1 free: no whole 1-core Drone fits now, eight would idle.
    busy = make_capacity(host=make_host_capacity(cpu_load=7.9 / 8))

    limits = sub_bee_limits(_inputs(cell_capacity=busy))

    assert limits.max_sub_bees == 0
    assert limits.limited_by is GrantBound.FREE_CORES
    assert limits.short() == frozenset({GrantBound.FREE_CORES})
    assert limits.never_lifts() == frozenset()
    assert limits.lasting is None
    assert limits.at_best[GrantBound.FREE_CORES] == 8


def test_a_reserve_that_claims_every_seat_never_lifts() -> None:
    reserve = RoyalReserve(seats=_SEATS, memory_bytes=0, headroom_fraction=0.1)

    limits = sub_bee_limits(_inputs(reserve=reserve))

    assert limits.max_sub_bees == 0
    assert limits.lasting is GrantBound.SEATS
    assert GrantBound.SEATS in limits.never_lifts()


def test_seats_busy_right_now_are_short_but_lift_once_the_calls_finish() -> None:
    # Every seat of four is in use by other bees' calls this instant; all four exist.
    busy = make_source(
        source_id="src_1",
        seats=_SEATS,
        abundance=Abundance(
            seats_free=0, requests_per_minute_left=None, tokens_per_minute_left=None
        ),
    )

    limits = sub_bee_limits(_inputs(map=ForageMap([busy], clock=FakeClock())))

    assert limits.short() == frozenset({GrantBound.SEATS})
    assert limits.never_lifts() == frozenset()


def test_a_host_whose_whole_memory_cannot_hold_one_bee_never_lifts() -> None:
    # 768 MiB in all, 512 MiB of it the Royal Reserve's: 256 MiB can never hold a 512 MiB Drone.
    host = make_host_capacity(memory_bytes=768 * _MIB, memory_free_bytes=768 * _MIB)

    limits = sub_bee_limits(_inputs(cell_capacity=make_capacity(host=host)))

    assert limits.lasting is GrantBound.FREE_MEMORY


def test_a_host_with_too_few_cores_for_one_bee_never_lifts() -> None:
    host = make_host_capacity(cores=1, cpu_load=0.0)
    two_core_role = make_footprint(cpu_cores=2.0)

    limits = sub_bee_limits(
        _inputs(cell_capacity=make_capacity(host=host), footprint=two_core_role)
    )

    assert limits.lasting is GrantBound.FREE_CORES


def test_a_cell_whose_own_cap_is_zero_never_lifts() -> None:
    limits = sub_bee_limits(_inputs(cell_capacity=make_capacity(max_sub_bees=0)))

    assert limits.lasting is GrantBound.CELL_CAP
    assert limits.limited_by is GrantBound.CELL_CAP


def test_the_goal_allowance_its_other_work_holds_lifts_when_that_work_is_done() -> None:
    budgets = replace(_BUDGETS, max_sub_bees=2)

    limits = sub_bee_limits(_inputs(budgets=budgets, goal_sub_bees_used=1))

    # One of two is left, and a 10% margin makes one whole bee of neither 1 nor 0.9.
    assert limits.limited_by is GrantBound.GOAL_BEES
    assert limits.short() == frozenset({GrantBound.GOAL_BEES})
    assert limits.never_lifts() == frozenset()


def test_goal_limit_matches_the_grants_own_goal_bound() -> None:
    budgets = replace(_BUDGETS, max_sub_bees=2)

    alone = goal_limit(budgets, 1, make_reserve(), Tempo())
    within = sub_bee_limits(_inputs(budgets=budgets, goal_sub_bees_used=1))

    assert alone.now == {GrantBound.GOAL_BEES: within.now[GrantBound.GOAL_BEES]}
    assert alone.at_best == {GrantBound.GOAL_BEES: within.at_best[GrantBound.GOAL_BEES]}
    assert alone.margin == within.margin
    assert alone.short() == frozenset({GrantBound.GOAL_BEES})
    assert goal_limit(budgets, 0, make_reserve(), Tempo()).short() == frozenset()


def test_a_goal_cap_of_one_bee_never_lifts_under_the_headroom_margin() -> None:
    limits = goal_limit(replace(_BUDGETS, max_sub_bees=1), 0, make_reserve(), Tempo())

    assert limits.lasting is GrantBound.GOAL_BEES


def test_an_urgent_tempo_keeps_more_of_the_margin_in_the_limits_too() -> None:
    urgent = Tempo(latency_budget_s=5.0)

    assert sub_bee_limits(_inputs(tempo=urgent)).margin > sub_bee_limits(_inputs()).margin


def test_the_tightest_limit_breaks_ties_in_declaration_order() -> None:
    # The cell cap and the goal's allowance are both 2; CELL_CAP is declared first.
    inputs = _inputs(
        cell_capacity=make_capacity(max_sub_bees=2), budgets=replace(_BUDGETS, max_sub_bees=2)
    )

    assert sub_bee_limits(inputs).limited_by is GrantBound.CELL_CAP


def test_the_grant_reason_names_the_tightest_limit() -> None:
    busy = make_capacity(host=make_host_capacity(cpu_load=7.9 / 8))

    assert "limited by free_cores" in grant(_inputs(cell_capacity=busy)).reason


# ──────────────────────────────────────────────────────────────────────────────
# Property: the limits are the grant's own, and no figure at its best allows fewer bees.
# ──────────────────────────────────────────────────────────────────────────────


@st.composite
def _any_inputs(draw: st.DrawFn) -> GrantInputs:
    """Draw GrantInputs across loads, memory, footprints, reserves, seats and goal usage."""
    memory = draw(st.integers(min_value=0, max_value=8 * _GIB))
    host = make_host_capacity(
        cores=draw(st.integers(min_value=1, max_value=32)),
        memory_bytes=memory,
        memory_free_bytes=draw(st.integers(min_value=0, max_value=memory)),
        cpu_load=draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
    )
    seats = draw(st.integers(min_value=0, max_value=8))
    free = Abundance(
        seats_free=draw(st.integers(min_value=0, max_value=8)),
        requests_per_minute_left=None,
        tokens_per_minute_left=None,
    )
    source = make_source(source_id="src_1", seats=seats, abundance=free)
    return _inputs(
        cell_capacity=make_capacity(
            host=host, max_sub_bees=draw(st.integers(min_value=0, max_value=16))
        ),
        footprint=make_footprint(
            cpu_cores=draw(st.floats(min_value=0.0, max_value=8.0, allow_nan=False)),
            memory_bytes=draw(st.integers(min_value=0, max_value=2 * _GIB)),
        ),
        reserve=make_reserve(
            seats=draw(st.integers(min_value=0, max_value=4)),
            memory_bytes=draw(st.integers(min_value=0, max_value=_GIB)),
            headroom_fraction=draw(st.floats(min_value=0.0, max_value=0.9, allow_nan=False)),
        ),
        map=ForageMap([source], clock=FakeClock()),
        budgets=replace(_BUDGETS, max_sub_bees=draw(st.integers(min_value=0, max_value=16))),
        goal_sub_bees_used=draw(st.integers(min_value=0, max_value=16)),
        tempo=Tempo(latency_budget_s=draw(st.one_of(st.none(), st.floats(1.0, 600.0)))),
    )


@given(_any_inputs())
def test_the_limits_are_always_the_grants_own_and_never_worse_at_best(inputs: GrantInputs) -> None:
    limits = sub_bee_limits(inputs)

    assert limits.max_sub_bees == grant(inputs).max_sub_bees
    assert all(limits.at_best[bound] >= limits.now[bound] for bound in GrantBound)
    # A limit that leaves no bee even at its best leaves none now either.
    assert limits.never_lifts() <= limits.short()
    # A grant with no bee always has a limit to blame: the tightest one is short.
    if limits.max_sub_bees < 1:
        assert limits.limited_by in limits.short()
