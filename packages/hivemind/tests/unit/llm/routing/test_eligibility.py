"""Tests for hivemind.llm.routing.eligibility: effective_floor, ineligible_cause and is_free.

Fits into the Hive:
    Mirrors src/hivemind/llm/routing/eligibility.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.llm.routing.eligibility for the module under test.
"""

from __future__ import annotations

import pytest
from builders.routing import make_chain, make_chain_source, make_routing_inputs

from hivemind.forage import AccuracyBar, ModelCost, ModelSlot, Tempo
from hivemind.llm.routing import PassOverCause, RoutingRules
from hivemind.llm.routing.eligibility import effective_floor, ineligible_cause, is_free
from waggle.clock import FakeClock
from waggle.ids import new_cell_id

_ROW = make_chain("a")[0]


@pytest.mark.parametrize(
    ("bar", "expected"),
    [
        (AccuracyBar.LOW, 2),  # The worker's own floor; LOW does not lower it.
        (AccuracyBar.NORMAL, 2),
        (AccuracyBar.HIGH, 3),
        (AccuracyBar.CRITICAL, 4),
    ],
)
def test_effective_floor_lets_tempo_raise_the_floor_but_never_lower_it(
    bar: AccuracyBar, expected: int
) -> None:
    inputs = make_routing_inputs([make_chain_source("a", grade=5)], tempo=Tempo(accuracy=bar))

    assert effective_floor(inputs) == expected


def test_effective_floor_does_not_lift_a_batch_slot_to_the_normal_bars_floor() -> None:
    inputs = make_routing_inputs([make_chain_source("a", grade=5)], slot=ModelSlot.ATTENDANT)

    assert effective_floor(inputs) == 1


def test_effective_floor_takes_the_larger_of_the_tempo_and_the_raised_floor() -> None:
    inputs = make_routing_inputs(
        [make_chain_source("a", grade=5)],
        tempo=Tempo(accuracy=AccuracyBar.HIGH),
        rules=RoutingRules().raise_floor(5),
    )

    assert effective_floor(inputs) == 5


def test_effective_floor_is_capped_at_the_best_effective_grade_on_the_map() -> None:
    sources = [make_chain_source("a", grade=5, measured_grade=3), make_chain_source("b", grade=2)]
    inputs = make_routing_inputs(sources, slot=ModelSlot.QUEEN)

    assert effective_floor(inputs) == 3  # Queen wants 5; the measured best is 3.


def test_effective_floor_is_capped_at_the_chains_best_not_the_maps() -> None:
    # The chain tops out at 3; a grade-5 model bound to some other slot is not reachable from it.
    sources = [
        make_chain_source("a", grade=3),
        make_chain_source("b", grade=3),
        make_chain_source("elsewhere", grade=5),
    ]
    inputs = make_routing_inputs(sources, tempo=Tempo(accuracy=AccuracyBar.CRITICAL))

    assert effective_floor(inputs) == 3  # CRITICAL wants 4; the chain's best is 3.


def test_effective_floor_falls_back_to_the_maps_best_when_no_chain_row_is_on_it() -> None:
    inputs = make_routing_inputs(
        [make_chain_source("elsewhere", grade=3)], tempo=Tempo(accuracy=AccuracyBar.CRITICAL)
    )

    assert effective_floor(inputs) == 3


def test_effective_floor_is_uncapped_when_the_map_is_empty() -> None:
    assert effective_floor(make_routing_inputs([], slot=ModelSlot.QUEEN)) == 5


def test_ineligible_cause_is_none_for_a_source_that_clears_every_check() -> None:
    source = make_chain_source("a")
    inputs = make_routing_inputs([source])

    assert ineligible_cause(_ROW, source, inputs, floor=2) is None


def test_ineligible_cause_reports_the_first_failing_check_in_a_fixed_order() -> None:
    # Fails capability, grade and cost at once; capability is checked first, then grade.
    source = make_chain_source("a", grade=1, cost=ModelCost(cost_per_million_input_usd=1.0))
    inputs = make_routing_inputs([source], required_capabilities=frozenset({"vision"}))

    assert ineligible_cause(_ROW, source, inputs, floor=2) is PassOverCause.CAPABILITY_MISSING
    plain = make_routing_inputs([source])
    assert ineligible_cause(_ROW, source, plain, floor=2) is PassOverCause.GRADE_BELOW_FLOOR


def test_ineligible_cause_checks_offline_even_for_an_unknown_source() -> None:
    inputs = make_routing_inputs([], rules=RoutingRules(offline=True))

    assert ineligible_cause(_ROW, None, inputs, floor=1) is PassOverCause.NOT_LOCAL_OFFLINE


def test_ineligible_cause_accepts_an_unknown_source_online() -> None:
    assert ineligible_cause(_ROW, None, make_routing_inputs([]), floor=5) is None


def test_ineligible_cause_reads_the_effective_grade_not_the_hand_set_one() -> None:
    source = make_chain_source("a", grade=1, measured_grade=4)

    assert ineligible_cause(_ROW, source, make_routing_inputs([source]), floor=3) is None


def test_is_free_for_a_zero_priced_source() -> None:
    assert is_free(make_chain_source("a")) is True


def test_is_free_rejects_a_token_priced_source() -> None:
    assert is_free(make_chain_source("a", cost=ModelCost(cost_per_million_output_usd=1.0))) is False


def test_is_free_treats_a_local_seat_hour_as_amortisation_and_a_hosted_one_as_spend() -> None:
    cost = ModelCost(cost_per_seat_hour_usd=2.0)
    cell = new_cell_id(FakeClock())

    assert is_free(make_chain_source("a", cost=cost, host_cell_id=cell)) is True
    assert is_free(make_chain_source("b", cost=cost)) is False
