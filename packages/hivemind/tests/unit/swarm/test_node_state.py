"""Tests for hivemind.swarm.node_state: whether the Hive can reach an enrolled device right now.

Fits into the Hive:
    Mirrors src/hivemind/swarm/node_state.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). A state machine is tested by walking it: every pair of statuses is tried, and the
    table says which ones are edges, so an edge added to the table without a reason, or taken out
    by accident, fails here.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.node_state for the module under test.
    - .claude/codingrules.md Appendix C for the machine.
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.swarm.errors import InvalidNodeTransitionError
from hivemind.swarm.node_state import TRANSITIONS, NodeStatus, assert_transition, can_transition

_EDGES = {
    (NodeStatus.ONLINE, NodeStatus.UNREACHABLE),  # The link dropped or beats were missed.
    (NodeStatus.ONLINE, NodeStatus.REVOKED),  # The operator revoked it.
    (NodeStatus.UNREACHABLE, NodeStatus.ONLINE),  # A heartbeat or reconnect restored it.
    (NodeStatus.UNREACHABLE, NodeStatus.REVOKED),  # Revoked while away.
}
_PAIRS = list(itertools.product(NodeStatus, NodeStatus))


def test_the_table_lists_exactly_the_four_edges_of_the_machine() -> None:
    listed = {(a, b) for a, targets in TRANSITIONS.items() for b in targets}

    assert listed == _EDGES and set(TRANSITIONS) == set(NodeStatus)


@pytest.mark.parametrize(("start", "end"), _PAIRS)
def test_every_pair_of_statuses_is_an_edge_exactly_when_the_table_says_so(
    start: NodeStatus, end: NodeStatus
) -> None:
    assert can_transition(start, end) is ((start, end) in _EDGES)


@pytest.mark.parametrize(("start", "end"), sorted(_EDGES, key=str))
def test_each_edge_may_be_walked(start: NodeStatus, end: NodeStatus) -> None:
    assert_transition(start, end, "device_x")


@pytest.mark.parametrize(("start", "end"), [pair for pair in _PAIRS if pair not in _EDGES], ids=str)
def test_every_other_move_raises_and_names_the_device(start: NodeStatus, end: NodeStatus) -> None:
    with pytest.raises(InvalidNodeTransitionError) as caught:
        assert_transition(start, end, "device_x")

    assert (caught.value.from_state, caught.value.to_state) == (start, end)
    assert "device_x" in str(caught.value)


def test_a_revoked_node_has_no_way_out() -> None:
    assert TRANSITIONS[NodeStatus.REVOKED] == frozenset()
    assert not any(can_transition(NodeStatus.REVOKED, status) for status in NodeStatus)


def test_the_error_reads_without_a_device_when_the_caller_has_none() -> None:
    with pytest.raises(InvalidNodeTransitionError) as caught:
        assert_transition(NodeStatus.REVOKED, NodeStatus.ONLINE)

    assert caught.value.device_id is None and "for device" not in str(caught.value)
