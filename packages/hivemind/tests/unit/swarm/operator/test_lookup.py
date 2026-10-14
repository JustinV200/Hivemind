"""Tests for hivemind.swarm.operator.lookup: how the operator's words name one enrolled device.

Fits into the Hive:
    Mirrors src/hivemind/swarm/operator/lookup.py (codingrules section 3). `find_node` is pure, so
    each test hands it a few `SwarmNode` rows from `builders.swarm` and reads what it returns or
    raises; the rule that matters most is the label's, because a revoked device keeps its label.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.swarm.operator.lookup for the module under test.
"""

from __future__ import annotations

import pytest
from builders.swarm import make_device_party, make_swarm_node

from hivemind.swarm.models import SwarmNode
from hivemind.swarm.node_state import NodeStatus
from hivemind.swarm.operator import AmbiguousNodeError, UnknownNodeError, find_node
from waggle.clock import FakeClock


def _node(clock: FakeClock, label: str | None, status: NodeStatus = NodeStatus.ONLINE) -> SwarmNode:
    """A device of its own, with this label and status."""
    extra = {"revoked_at": clock.now()} if status is NodeStatus.REVOKED else {}
    return make_swarm_node(make_device_party(clock), clock, label=label, status=status, **extra)


def test_find_node_names_a_device_by_its_device_id_or_its_cell_id() -> None:
    clock = FakeClock()
    nodes = [_node(clock, "kitchen-pi"), _node(clock, "attic-pc")]

    by_device = find_node(nodes, nodes[1].device_id)
    by_cell = find_node(nodes, nodes[0].cell_id)

    assert by_device is nodes[1]
    assert by_cell is nodes[0]


def test_find_node_names_a_device_by_its_label_and_ignores_surrounding_space() -> None:
    clock = FakeClock()
    nodes = [_node(clock, "kitchen-pi"), _node(clock, "attic-pc")]

    assert find_node(nodes, "  attic-pc\n") is nodes[1]


def test_find_node_prefers_the_live_device_when_a_revoked_one_kept_the_label() -> None:
    clock = FakeClock()
    old = _node(clock, "kitchen-pi", NodeStatus.REVOKED)
    new = _node(clock, "kitchen-pi")

    assert find_node([old, new], "kitchen-pi") is new
    assert find_node([new, old], "kitchen-pi") is new


def test_find_node_falls_back_to_a_revoked_device_when_it_is_the_only_one() -> None:
    clock = FakeClock()
    old = _node(clock, "kitchen-pi", NodeStatus.REVOKED)

    assert find_node([old], "kitchen-pi") is old


def test_find_node_refuses_a_label_two_live_devices_share_and_names_both() -> None:
    clock = FakeClock()
    first, second = _node(clock, "pi"), _node(clock, "pi")

    with pytest.raises(AmbiguousNodeError) as refused:
        find_node([first, second], "pi")

    assert refused.value.device_ids == (first.device_id, second.device_id)
    assert first.device_id in str(refused.value) and second.device_id in str(refused.value)


def test_find_node_refuses_a_label_that_only_several_revoked_devices_share() -> None:
    clock = FakeClock()
    nodes = [_node(clock, "pi", NodeStatus.REVOKED), _node(clock, "pi", NodeStatus.REVOKED)]

    with pytest.raises(AmbiguousNodeError):
        find_node(nodes, "pi")


def test_find_node_lets_an_id_win_over_a_label_that_spells_another_devices_id() -> None:
    clock = FakeClock()
    plain = _node(clock, "kitchen-pi")
    sneaky = _node(clock, str(plain.device_id))

    assert find_node([sneaky, plain], plain.device_id) is plain


@pytest.mark.parametrize("reference", ["", "   ", "nobody", "device_01ARZ3NDEKTSV4RRFFQ69G5FAV"])
def test_find_node_refuses_a_reference_that_matches_nothing_and_a_blank_one(
    reference: str,
) -> None:
    clock = FakeClock()
    nodes = [_node(clock, "kitchen-pi"), _node(clock, None)]

    with pytest.raises(UnknownNodeError) as refused:
        find_node(nodes, reference)

    assert "hive swarm list" in str(refused.value)


def test_find_node_never_matches_a_blank_reference_to_a_device_without_a_label() -> None:
    clock = FakeClock()

    with pytest.raises(UnknownNodeError):
        find_node([_node(clock, None)], " ")
