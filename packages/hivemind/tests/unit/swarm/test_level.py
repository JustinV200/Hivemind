"""Tests for hivemind.swarm.level: the runtime ladder a device climbs one rung at a time.

Fits into the Hive:
    Mirrors src/hivemind/swarm/level.py (codingrules section 3: tests/unit mirrors src/ one-to-one).
    The ladder is GATEWAY <-> COLONIZED <-> NUC, and a Warden moves onto a device before a model
    server does, so GATEWAY and NUC are never neighbours. Every pair of rungs is tried against the
    table, in the same way node_state is walked. The decision whether to colonize a device is
    wave 3's (`decide_colonize`); this file covers the transitions only.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.level for the module under test.
    - .claude/codingrules.md Appendix C, "Runtime ladder".
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.swarm.errors import LadderError
from hivemind.swarm.level import TRANSITIONS, assert_move, can_move, is_promotion
from waggle.messages.swarm import RuntimeLevel

_UP = {
    (RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED),  # The Warden moves onto the device.
    (RuntimeLevel.COLONIZED, RuntimeLevel.NUC),  # A model server starts under the Warden.
}
_DOWN = {
    (RuntimeLevel.COLONIZED, RuntimeLevel.GATEWAY),  # Withdrawal: the on-device Warden stops.
    (RuntimeLevel.NUC, RuntimeLevel.COLONIZED),  # Demotion: the model server stops.
}
_EDGES = _UP | _DOWN
_PAIRS = list(itertools.product(RuntimeLevel, RuntimeLevel))


def test_the_table_lists_exactly_the_four_edges_of_the_ladder() -> None:
    listed = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}

    assert listed == _EDGES and set(TRANSITIONS) == set(RuntimeLevel)


@pytest.mark.parametrize(("start", "end"), _PAIRS)
def test_every_pair_of_rungs_is_a_move_exactly_when_the_table_says_so(
    start: RuntimeLevel, end: RuntimeLevel
) -> None:
    assert can_move(start, end) is ((start, end) in _EDGES)


@pytest.mark.parametrize(("start", "end"), sorted(_EDGES, key=str))
def test_each_edge_may_be_walked(start: RuntimeLevel, end: RuntimeLevel) -> None:
    assert_move(start, end, "device_x")


@pytest.mark.parametrize(("start", "end"), [p for p in _PAIRS if p not in _EDGES], ids=str)
def test_every_other_move_raises_and_names_the_device(
    start: RuntimeLevel, end: RuntimeLevel
) -> None:
    with pytest.raises(LadderError) as caught:
        assert_move(start, end, "device_x")

    assert (caught.value.from_level, caught.value.to_level) == (start, end)
    assert "device_x" in str(caught.value)


def test_the_gateway_and_the_nuc_are_never_neighbours() -> None:
    assert not can_move(RuntimeLevel.GATEWAY, RuntimeLevel.NUC)
    assert not can_move(RuntimeLevel.NUC, RuntimeLevel.GATEWAY)


def test_staying_where_it_is_is_not_a_move() -> None:
    for rung in RuntimeLevel:
        with pytest.raises(LadderError):
            assert_move(rung, rung)


@pytest.mark.parametrize(("start", "end"), sorted(_UP, key=str))
def test_an_edge_up_is_a_promotion(start: RuntimeLevel, end: RuntimeLevel) -> None:
    assert is_promotion(start, end)


@pytest.mark.parametrize(("start", "end"), sorted(_DOWN, key=str))
def test_an_edge_down_is_a_demotion(start: RuntimeLevel, end: RuntimeLevel) -> None:
    assert not is_promotion(start, end)


def test_the_error_reads_without_a_device_when_the_caller_has_none() -> None:
    with pytest.raises(LadderError) as caught:
        assert_move(RuntimeLevel.GATEWAY, RuntimeLevel.NUC)

    assert caught.value.device_id is None and "for device" not in str(caught.value)
