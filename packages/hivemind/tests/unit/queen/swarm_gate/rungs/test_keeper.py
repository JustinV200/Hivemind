"""Tests for hivemind.queen.swarm_gate.rungs.keeper: what the Queen remembers of her own moves.

The decision is pure, so the memory it reads has to be right: the mark of a rung she raised (so a
rung the operator raised is never hers to take back), the wait before a failed move is tried again
(doubling with each failure in a row, up to a ceiling, and cleared by a success), and the refusal
she has already recorded. A move she launches is settled on a later pass by the rung the device is
on, so these tests drive `begin` and `settle` the way the evaluator does, on a clock that is a fact.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/keeper.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.keeper for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.swarm import NOW, make_swarm_node
from builders.swarm.gate import gate_terms

from hivemind.manifest.schema.swarm import MAX_RETRY_DOUBLINGS
from hivemind.queen.swarm_gate.rungs import RungKeeper
from hivemind.swarm import RuntimeLevel, SwarmNode

G, C, N = RuntimeLevel.GATEWAY, RuntimeLevel.COLONIZED, RuntimeLevel.NUC
_RETRY_S = 100.0  # `[swarm.ladder] retry_after_s` in these terms.
_NUC_RETRY_S = 40.0  # `[swarm.nuc] retry_after_s`.


def _keeper() -> RungKeeper:
    return RungKeeper(gate_terms(retry_after_s=_RETRY_S, nuc_retry_after_s=_NUC_RETRY_S))


def _on(level: RuntimeLevel) -> SwarmNode:
    return make_swarm_node(level=level)


def test_a_keeper_that_has_seen_nothing_remembers_nothing() -> None:
    node = _on(G)

    memory = _keeper().memory(node.device_id)

    assert memory.seen == {} and memory.retry_at == {}


def test_a_rung_that_was_marked_is_remembered_with_the_time_it_was_seen() -> None:
    keeper, node = _keeper(), _on(C)

    keeper.mark_seen(node.device_id, C, NOW)

    assert keeper.memory(node.device_id).seen == {C: NOW}


def test_a_move_that_reached_its_rung_marks_it_as_hers() -> None:
    keeper, node = _keeper(), _on(G)
    keeper.begin(node.device_id, (G, C), "relief")
    later = NOW + timedelta(minutes=2)

    keeper.settle(node.model_copy(update={"level": C}), later)

    assert keeper.memory(node.device_id).seen == {C: later}
    assert keeper.memory(node.device_id).retry_at == {}
    assert not keeper.pending("relief")


def test_a_move_in_flight_is_pending_until_it_is_settled() -> None:
    keeper, node = _keeper(), _on(G)

    keeper.begin(node.device_id, (G, C), "relief")

    assert keeper.pending("relief") and not keeper.pending("outage")
    keeper.settle(node, NOW)
    assert not keeper.pending("relief")


def test_a_move_that_left_the_device_where_it_was_failed_and_is_backed_off() -> None:
    keeper, node = _keeper(), _on(G)
    keeper.begin(node.device_id, (G, C), "relief")

    keeper.settle(node, NOW)  # Still on the first rung: the flow failed and undid itself.

    assert keeper.memory(node.device_id).retry_at == {(G, C): NOW + timedelta(seconds=_RETRY_S)}
    assert keeper.memory(node.device_id).seen == {}  # And nothing was raised, so nothing is marked.


def test_each_failure_in_a_row_doubles_the_wait() -> None:
    keeper, node = _keeper(), _on(G)
    waits = []

    for _ in range(4):
        keeper.begin(node.device_id, (G, C), "relief")
        keeper.settle(node, NOW)
        waits.append(keeper.memory(node.device_id).retry_at[(G, C)] - NOW)

    assert [wait.total_seconds() for wait in waits] == [100.0, 200.0, 400.0, 800.0]


def test_the_wait_stops_doubling_at_the_ceiling() -> None:
    keeper, node = _keeper(), _on(G)

    for _ in range(MAX_RETRY_DOUBLINGS + 4):
        keeper.failed(node.device_id, (G, C), NOW)

    wait = keeper.memory(node.device_id).retry_at[(G, C)] - NOW
    assert wait.total_seconds() == _RETRY_S * 2**MAX_RETRY_DOUBLINGS  # 32 times the base.


def test_a_success_clears_the_wait_and_the_count() -> None:
    keeper, node = _keeper(), _on(G)
    keeper.failed(node.device_id, (G, C), NOW)
    keeper.failed(node.device_id, (G, C), NOW)
    keeper.begin(node.device_id, (G, C), "relief")
    keeper.settle(node.model_copy(update={"level": C}), NOW)

    keeper.failed(node.device_id, (G, C), NOW)  # A new failure starts again at the base.

    assert keeper.memory(node.device_id).retry_at[(G, C)] == NOW + timedelta(seconds=_RETRY_S)


def test_a_nuc_move_waits_by_its_own_time() -> None:
    keeper, node = _keeper(), _on(C)

    keeper.failed(node.device_id, (C, N), NOW)  # Up to a Nuc.
    keeper.failed(node.device_id, (N, C), NOW)  # And back down.

    retry = keeper.memory(node.device_id).retry_at
    assert retry[(C, N)] == NOW + timedelta(seconds=_NUC_RETRY_S)
    assert retry[(N, C)] == NOW + timedelta(seconds=_NUC_RETRY_S)


def test_failures_of_two_moves_and_two_devices_are_counted_apart() -> None:
    keeper, one, two = _keeper(), _on(G), _on(G)

    keeper.failed(one.device_id, (G, C), NOW)
    keeper.failed(one.device_id, (G, C), NOW)
    keeper.failed(two.device_id, (G, C), NOW)
    keeper.failed(one.device_id, (C, G), NOW)

    assert keeper.memory(one.device_id).retry_at[(G, C)] == NOW + timedelta(seconds=2 * _RETRY_S)
    assert keeper.memory(one.device_id).retry_at[(C, G)] == NOW + timedelta(seconds=_RETRY_S)
    assert keeper.memory(two.device_id).retry_at[(G, C)] == NOW + timedelta(seconds=_RETRY_S)


def test_a_rung_taken_back_loses_its_mark() -> None:
    keeper, node = _keeper(), _on(C)
    keeper.mark_seen(node.device_id, C, NOW)
    keeper.begin(node.device_id, (C, G), "reasons_gone")

    keeper.settle(node.model_copy(update={"level": G}), NOW + timedelta(hours=1))

    assert keeper.memory(node.device_id).seen == {}


def test_a_nuc_taken_back_gives_the_colonized_rung_the_whole_wait_again() -> None:
    keeper, node = _keeper(), _on(N)
    keeper.mark_seen(node.device_id, C, NOW)  # She raised it to colonized long ago...
    keeper.mark_seen(node.device_id, N, NOW + timedelta(hours=1))  # ... and later to a Nuc.
    keeper.begin(node.device_id, (N, C), "reasons_gone")
    lowered_at = NOW + timedelta(hours=3)

    keeper.settle(node.model_copy(update={"level": C}), lowered_at)

    assert keeper.memory(node.device_id).seen == {C: lowered_at}  # Not withdrawn at once.


def test_a_nuc_the_operator_demoted_does_not_stay_marked_as_hers() -> None:
    keeper, node = _keeper(), _on(N)
    keeper.mark_seen(node.device_id, N, NOW)

    keeper.settle(node.model_copy(update={"level": C}), NOW)  # Nothing of hers began this.

    assert keeper.memory(node.device_id).seen == {}  # If the operator lifts it again, it is theirs.


def test_marks_on_or_below_the_rung_the_device_is_on_are_kept() -> None:
    keeper, node = _keeper(), _on(N)
    keeper.mark_seen(node.device_id, C, NOW)
    keeper.mark_seen(node.device_id, N, NOW)

    keeper.settle(node, NOW)

    assert set(keeper.memory(node.device_id).seen) == {C, N}


def test_settling_a_device_with_nothing_launched_changes_nothing() -> None:
    keeper, node = _keeper(), _on(C)

    keeper.settle(node, NOW)

    assert keeper.memory(node.device_id).retry_at == {}


def test_a_refusal_is_news_once_until_it_changes_or_goes_away() -> None:
    keeper, node = _keeper(), _on(G)

    assert keeper.declined(node.device_id, "python_too_old") is True
    assert keeper.declined(node.device_id, "python_too_old") is False  # Not recorded again.
    assert keeper.declined(node.device_id, "access_too_low") is True  # A different word is news.
    assert keeper.declined(node.device_id, None) is False  # Nothing is refused now...
    assert keeper.declined(node.device_id, "access_too_low") is True  # ... so it is news again.
