"""Tests for hivemind.llm.routing.policy: choose_binding over a worker slot's fallback chain.

Fits into the Hive:
    Mirrors src/hivemind/llm/routing/policy.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.routing.policy for the module under test.
    - tests/builders/routing.py for the chain and inputs builders.
"""

from __future__ import annotations

from datetime import UTC, datetime

from builders.forage import make_source
from builders.llm import make_binding
from builders.routing import make_chain_source, make_routing_inputs
from hypothesis import given
from hypothesis import strategies as st

from hivemind.forage import (
    Abundance,
    AccuracyBar,
    Distance,
    Effort,
    HostingPlan,
    ModelCost,
    ModelSlot,
    ModelSource,
    SlotPlan,
    SourceChain,
    Tempo,
)
from hivemind.llm.routing import (
    PassedOver,
    PassOverCause,
    RoutingRules,
    choose_binding,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id

_CLOCK = FakeClock()
_CELL = new_cell_id(_CLOCK)
_OTHER_CELL = new_cell_id(_CLOCK)
_PRICED = ModelCost(cost_per_million_input_usd=3.0, cost_per_million_output_usd=15.0)


def _seats(free: int) -> Abundance:
    return Abundance(seats_free=free)


def _distance(latency_s: float) -> Distance:
    return Distance(latency_s=latency_s, tokens_per_s=50.0, measured_at=datetime.now(UTC))


def test_choose_binding_picks_the_manifest_primary_when_every_binding_ties() -> None:
    inputs = make_routing_inputs([make_chain_source("a"), make_chain_source("b")])

    decision = choose_binding(inputs)

    assert (decision.key, decision.order) == ("worker", ("worker", "b"))
    assert decision.passed_over == ()
    assert decision.forage_shortfall is None


def test_choose_binding_prefers_the_cheaper_of_two_eligible_sources() -> None:
    sources = [make_chain_source("a", cost=_PRICED), make_chain_source("b")]
    inputs = make_routing_inputs(sources, rules=RoutingRules(spend_cap_usd=5.0))

    decision = choose_binding(inputs)

    assert decision.key == "b"
    assert decision.order == ("b", "worker")


def test_choose_binding_prefers_the_closer_source_when_price_ties() -> None:
    sources = [
        make_chain_source("a", distance=_distance(2.0)),
        make_chain_source("b", distance=_distance(0.5)),
    ]

    decision = choose_binding(make_routing_inputs(sources))

    assert decision.key == "b"


def test_choose_binding_prefers_a_source_on_the_bees_own_cell_over_a_cheaper_remote_one() -> None:
    sources = [make_chain_source("a", host_cell_id=_CELL, cost=_PRICED), make_chain_source("b")]
    inputs = make_routing_inputs(
        sources, cell_id=_CELL, rules=RoutingRules(spend_cap_usd=5.0), keys=("b", "a")
    )

    decision = choose_binding(inputs)

    # Chain is worker(b) -> a; "a" runs on the bee's Cell, so it leads despite its price.
    assert decision.key == "a"
    assert decision.reason.local is True


def test_choose_binding_does_not_count_another_cells_source_as_local() -> None:
    sources = [make_chain_source("a", host_cell_id=_OTHER_CELL), make_chain_source("b")]

    decision = choose_binding(make_routing_inputs(sources, cell_id=_CELL))

    assert decision.key == "worker"
    assert decision.reason.local is False


def test_choose_binding_passes_over_a_source_below_the_slots_floor() -> None:
    sources = [make_chain_source("a", grade=1), make_chain_source("b", grade=3)]

    decision = choose_binding(make_routing_inputs(sources))

    assert decision.key == "b"
    assert decision.passed_over == (PassedOver("worker", PassOverCause.GRADE_BELOW_FLOOR),)
    assert decision.reason.floor == 2


def test_choose_binding_lets_a_high_accuracy_bar_raise_the_floor() -> None:
    sources = [make_chain_source("a", grade=2), make_chain_source("b", grade=3)]
    inputs = make_routing_inputs(sources, tempo=Tempo(accuracy=AccuracyBar.HIGH))

    decision = choose_binding(inputs)

    assert decision.key == "b"
    assert decision.reason.floor == 3


def test_choose_binding_never_lets_a_low_accuracy_bar_lower_the_floor() -> None:
    sources = [make_chain_source("a", grade=1), make_chain_source("b", grade=3)]
    inputs = make_routing_inputs(sources, tempo=Tempo(accuracy=AccuracyBar.LOW))

    decision = choose_binding(inputs)

    assert decision.key == "b"  # The worker slot's own floor of 2 still stands.


def test_choose_binding_applies_a_rebinds_raised_floor() -> None:
    sources = [make_chain_source("a", grade=2), make_chain_source("b", grade=4)]
    rules = RoutingRules().raise_floor(4)

    decision = choose_binding(make_routing_inputs(sources, rules=rules))

    assert decision.key == "b"


def test_choose_binding_caps_the_queens_floor_at_the_best_grade_on_the_map() -> None:
    sources = [make_chain_source("a", grade=4), make_chain_source("b", grade=2)]
    queen_chain = (
        make_binding(key="queen", provider="a", model="a", fallback="b"),
        make_binding(key="b", provider="b", model="b"),
    )
    inputs = make_routing_inputs(sources, slot=ModelSlot.QUEEN, bindings=queen_chain)

    decision = choose_binding(inputs)

    # The queen's floor is 5 but nothing on the map is better than 4: grade 4 qualifies.
    assert decision.key == "queen"
    assert decision.reason.floor == 4
    assert decision.reason.fell_back is False


def test_choose_binding_passes_over_a_source_missing_a_required_capability() -> None:
    sources = [
        make_chain_source("a", capabilities=("tools",)),
        make_chain_source("b", capabilities=("tools", "vision")),
    ]
    inputs = make_routing_inputs(sources, required_capabilities=frozenset({"vision"}))

    decision = choose_binding(inputs)

    assert decision.key == "b"
    assert decision.passed_over == (PassedOver("worker", PassOverCause.CAPABILITY_MISSING),)


def test_choose_binding_passes_over_a_source_past_the_latency_budget() -> None:
    sources = [make_chain_source("a", distance=_distance(30.0)), make_chain_source("b")]
    inputs = make_routing_inputs(sources, tempo=Tempo(latency_budget_s=20.0))

    decision = choose_binding(inputs)

    assert decision.key == "b"
    assert decision.passed_over == (PassedOver("worker", PassOverCause.TOO_FAR),)


def test_choose_binding_does_not_call_an_unmeasured_source_too_far() -> None:
    inputs = make_routing_inputs(
        [make_chain_source("a"), make_chain_source("b")], tempo=Tempo(latency_budget_s=1.0)
    )

    assert choose_binding(inputs).key == "worker"


def test_choose_binding_passes_over_a_paid_source_when_the_spend_cap_is_zero() -> None:
    sources = [make_chain_source("a", cost=_PRICED), make_chain_source("b")]

    decision = choose_binding(make_routing_inputs(sources, rules=RoutingRules(spend_cap_usd=0.0)))

    assert decision.key == "b"
    assert decision.passed_over == (PassedOver("worker", PassOverCause.OVER_COST),)


def test_choose_binding_allows_a_paid_source_when_the_spend_cap_is_positive() -> None:
    sources = [make_chain_source("a", cost=_PRICED), make_chain_source("b", grade=1)]

    decision = choose_binding(make_routing_inputs(sources, rules=RoutingRules(spend_cap_usd=1.0)))

    assert decision.key == "worker"


def test_choose_binding_passes_over_a_provider_outside_the_hive_when_offline() -> None:
    sources = [make_chain_source("a"), make_chain_source("b")]
    inputs = make_routing_inputs(
        sources, rules=RoutingRules(offline=True), local_providers=frozenset({"b"})
    )

    decision = choose_binding(inputs)

    assert decision.key == "b"
    assert decision.passed_over == (PassedOver("worker", PassOverCause.NOT_LOCAL_OFFLINE),)


def test_choose_binding_ranks_a_seatless_source_last_among_eligible_peers() -> None:
    sources = [make_chain_source("a", abundance=_seats(0)), make_chain_source("b")]

    decision = choose_binding(make_routing_inputs(sources))

    assert decision.key == "b"
    assert decision.order == ("b", "worker")
    assert decision.passed_over == (PassedOver("worker", PassOverCause.NO_SEAT),)
    assert decision.forage_shortfall is None


def test_choose_binding_files_a_shortfall_when_the_chosen_source_has_no_seat() -> None:
    sources = [make_chain_source("a", abundance=_seats(0)), make_chain_source("b", grade=1)]

    decision = choose_binding(make_routing_inputs(sources))

    assert decision.key == "worker"
    assert decision.forage_shortfall is not None
    assert decision.forage_shortfall.source_id == "src_a"
    assert decision.forage_shortfall.seats_wanted == 1


def test_choose_binding_treats_a_source_offering_no_seats_at_all_as_seatless() -> None:
    sources = [make_chain_source("a", seats=0), make_chain_source("b")]

    assert choose_binding(make_routing_inputs(sources)).key == "b"


def test_choose_binding_keeps_a_binding_unknown_to_forage_but_ranks_it_last() -> None:
    inputs = make_routing_inputs([make_chain_source("b")])  # "a" has no map source.

    decision = choose_binding(inputs)

    assert decision.key == "b"
    assert decision.order == ("b", "worker")
    assert decision.passed_over == (PassedOver("worker", PassOverCause.NOT_ON_MAP),)


def test_choose_binding_chooses_an_unknown_binding_when_it_is_all_there_is() -> None:
    decision = choose_binding(make_routing_inputs([]))

    assert decision.key == "worker"
    assert decision.reason.chosen_grade is None
    assert decision.reason.fell_back is False
    assert decision.forage_shortfall is None


def test_choose_binding_ranks_a_hosting_plan_chain_ahead_of_a_cheaper_source_outside_it() -> None:
    sources = [make_chain_source("a", cost=_PRICED), make_chain_source("b")]
    plan = _plan(["src_a"])
    inputs = make_routing_inputs(sources, hosting=plan, rules=RoutingRules(spend_cap_usd=5.0))

    decision = choose_binding(inputs)

    assert decision.key == "worker"
    assert decision.reason.planned is True


def test_choose_binding_orders_plan_sources_by_their_place_in_the_plan_chain() -> None:
    sources = [make_chain_source("a"), make_chain_source("b")]
    inputs = make_routing_inputs(sources, hosting=_plan(["src_b", "src_a"]))

    decision = choose_binding(inputs)

    assert decision.order == ("b", "worker")


def test_choose_binding_ignores_a_plan_that_names_no_chain_for_the_slot() -> None:
    sources = [make_chain_source("a", cost=_PRICED), make_chain_source("b")]
    plan = _plan(["src_a"], slot=ModelSlot.JUDGE)
    inputs = make_routing_inputs(sources, hosting=plan, rules=RoutingRules(spend_cap_usd=5.0))

    assert choose_binding(inputs).key == "b"  # Cheaper wins; the plan has no say for WORKER.


def _plan(source_ids: list[str], slot: ModelSlot = ModelSlot.WORKER) -> HostingPlan:
    """Build a hosting plan whose chain for `slot` is `source_ids`, primary first."""
    chain = SourceChain(primary=source_ids[0], fallbacks=tuple(source_ids[1:]))
    return HostingPlan(
        cell_id=_CELL, slots=(SlotPlan(slot=slot, chain=chain),), default=chain, reason="test plan"
    )


def test_choose_binding_falls_back_to_the_manifest_primary_when_nothing_is_eligible() -> None:
    # A grade floor is capped at what the chain holds, so it can never rule out every row; a
    # capability the task needs and no row has can, and then the chain still answers.
    sources = [make_chain_source("a"), make_chain_source("b")]

    decision = choose_binding(
        make_routing_inputs(sources, required_capabilities=frozenset({"vision"}))
    )

    assert decision.key == "worker"
    assert decision.order == ("worker", "b")
    assert decision.reason.fell_back is True
    assert decision.reason.eligible == 0
    assert [p.cause for p in decision.passed_over] == [PassOverCause.CAPABILITY_MISSING] * 2
    assert decision.forage_shortfall is None


def test_choose_binding_ignores_a_strong_source_outside_the_chain_when_capping_the_floor() -> None:
    # The review's probe: a Worker chain of grade 3s, its primary with no seat. With only those
    # on the map CRITICAL routes to `b`; the Queen's off-chain grade-5 model must not change that.
    primary = make_chain_source("a", grade=3, abundance=_seats(0))
    sources = [primary, make_chain_source("b", grade=3)]
    queen = make_source(source_id="src_claude", provider="anthropic", model="opus", grade=5)
    tempo = Tempo(accuracy=AccuracyBar.CRITICAL)

    alone = choose_binding(make_routing_inputs(sources, tempo=tempo))
    with_queen = choose_binding(make_routing_inputs([*sources, queen], tempo=tempo))

    assert (alone.key, alone.reason.fell_back, alone.reason.floor) == ("b", False, 3)
    assert (with_queen.key, with_queen.reason.fell_back, with_queen.reason.floor) == ("b", False, 3)


def test_choose_binding_ranks_the_ineligible_tail_by_the_same_key_when_falling_back() -> None:
    # Both rows lack the capability; the primary also has no seat, so the fallback choice is the
    # one that can actually take the call, with the primary behind it, not manifest order.
    sources = [make_chain_source("a", abundance=_seats(0)), make_chain_source("b")]

    decision = choose_binding(
        make_routing_inputs(sources, required_capabilities=frozenset({"vision"}))
    )

    assert decision.reason.fell_back is True
    assert decision.key == "b"
    assert decision.order == ("b", "worker")


def test_choose_binding_lets_a_measured_grade_override_the_hand_set_one() -> None:
    # Hand-set 5 would clear any floor; the eval harness measured it at 1, which is below 2.
    measured = make_chain_source("a", grade=5, measured_grade=1)
    sources = [measured, make_chain_source("b", grade=3)]

    decision = choose_binding(make_routing_inputs(sources))

    assert decision.key == "b"
    assert decision.passed_over[0].cause is PassOverCause.GRADE_BELOW_FLOOR


def test_choose_binding_reports_the_chosen_sources_effective_grade() -> None:
    sources = [make_chain_source("a", grade=2, measured_grade=4), make_chain_source("b")]

    assert choose_binding(make_routing_inputs(sources)).reason.chosen_grade == 4


def test_choose_binding_sets_effort_from_tempo_and_the_chosen_rows_own_effort() -> None:
    sources = [make_chain_source("a"), make_chain_source("b")]

    normal = choose_binding(make_routing_inputs(sources))
    urgent = choose_binding(make_routing_inputs(sources, tempo=Tempo(latency_budget_s=5.0)))

    assert normal.effort is Effort.MEDIUM  # make_binding's default row effort.
    assert urgent.effort is Effort.LOW


def test_choose_binding_is_deterministic_for_equal_inputs() -> None:
    sources = [make_chain_source("a"), make_chain_source("b", abundance=_seats(0))]

    first = choose_binding(make_routing_inputs(sources))
    second = choose_binding(make_routing_inputs(sources))

    assert first == second


def test_render_describes_the_choice_within_the_reason_bound() -> None:
    decision = choose_binding(make_routing_inputs([make_chain_source("a")]))

    text = decision.reason.render()

    assert "eligible" in text and "floor 2" in text


# ──────────────────────────────────────────────────────────────────────────────
# Properties
# ──────────────────────────────────────────────────────────────────────────────

_NAMES = ("a", "b", "c", "d")


@st.composite
def _worlds(draw: st.DrawFn) -> tuple[list[str], list[ModelSource]]:
    """Draw a chain of 1-4 names and a map source for most of them (the rest are unknown)."""
    keys = list(_NAMES[: draw(st.integers(min_value=1, max_value=4))])
    sources = [
        make_chain_source(
            name,
            grade=draw(st.integers(min_value=1, max_value=5)),
            abundance=_seats(draw(st.integers(min_value=0, max_value=2))),
            cost=ModelCost(cost_per_million_input_usd=draw(st.sampled_from([0.0, 1.0, 5.0]))),
        )
        for name in keys
        if draw(st.integers(min_value=0, max_value=4)) > 0  # About one in five has no source.
    ]
    return keys, sources


@given(world=_worlds(), cap=st.sampled_from([0.0, 10.0]))
def test_choose_binding_always_chooses_from_the_chain_and_permutes_it(
    world: tuple[list[str], list[ModelSource]], cap: float
) -> None:
    keys, sources = world
    inputs = make_routing_inputs(sources, keys=keys, rules=RoutingRules(spend_cap_usd=cap))

    decision = choose_binding(inputs)

    chain = ["worker", *keys[1:]]
    assert decision.key in chain
    assert sorted(decision.order) == sorted(chain)
    assert decision.order[0] == decision.key


@given(world=_worlds(), low=st.integers(0, 5), high=st.integers(0, 5))
def test_raising_the_floor_never_lowers_the_chosen_grade(
    world: tuple[list[str], list[ModelSource]], low: int, high: int
) -> None:
    keys, sources = world
    low, high = sorted((low, high))
    rules = RoutingRules(spend_cap_usd=10.0)
    before = choose_binding(make_routing_inputs(sources, keys=keys, rules=rules.raise_floor(low)))
    after = choose_binding(make_routing_inputs(sources, keys=keys, rules=rules.raise_floor(high)))

    # Only comparable while both decisions chose a known, eligible source: a fallback promises
    # nothing, and an unknown source has no grade to compare.
    before_grade, after_grade = before.reason.chosen_grade, after.reason.chosen_grade
    comparable = not (before.reason.fell_back or after.reason.fell_back)
    if comparable and before_grade is not None and after_grade is not None:
        assert after_grade >= before_grade
