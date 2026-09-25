"""Tests for hivemind.wardens.state: the WardenState machine and its wire mirror.

Fits into the Hive:
    Mirrors src/hivemind/wardens/state.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.state for the module under test.
    - .claude/codingrules.md Appendix C, "Warden" row, for the transition table asserted here.
"""

from __future__ import annotations

import pytest
from builders.workers import make_assignment

from hivemind.wardens.errors import InvalidWardenTransitionError
from hivemind.wardens.state import (
    TRANSITIONS,
    WardenState,
    assert_transition,
    can_transition,
    clustering_update,
    is_terminal,
    settled_state,
)
from waggle.ids import TaskId
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.supervision import WardenState as WireWardenState
from waggle.messages.task import TaskCancel, TaskResume

# Every allowed edge, read literally off this module's own table (module docstring's own
# expansion of Appendix C's compressed notation).
_ALLOWED_EDGES: tuple[tuple[WardenState, WardenState], ...] = (
    (WardenState.STARTING, WardenState.ACTIVE),
    (WardenState.STARTING, WardenState.WATCH),
    (WardenState.ACTIVE, WardenState.WATCH),
    (WardenState.ACTIVE, WardenState.OFFLINE),
    (WardenState.ACTIVE, WardenState.CLUSTERED),
    (WardenState.ACTIVE, WardenState.MIGRATING),
    (WardenState.ACTIVE, WardenState.STOPPED),
    (WardenState.WATCH, WardenState.ACTIVE),
    (WardenState.WATCH, WardenState.OFFLINE),
    (WardenState.WATCH, WardenState.CLUSTERED),
    (WardenState.WATCH, WardenState.STOPPED),
    (WardenState.OFFLINE, WardenState.ACTIVE),
    (WardenState.OFFLINE, WardenState.CLUSTERED),
    (WardenState.OFFLINE, WardenState.STOPPED),
    (WardenState.CLUSTERED, WardenState.ACTIVE),
    (WardenState.CLUSTERED, WardenState.STOPPED),
    (WardenState.MIGRATING, WardenState.ACTIVE),
    (WardenState.MIGRATING, WardenState.STOPPED),
)


def test_warden_state_mirrors_the_wire_form_member_for_member() -> None:
    assert {member.name for member in WardenState} == {member.name for member in WireWardenState}
    for member in WardenState:
        assert member.value == WireWardenState[member.name].value


@pytest.mark.parametrize("member", list(WardenState))
def test_from_wire_and_to_wire_round_trip(member: WardenState) -> None:
    wire = member.to_wire()
    assert WardenState.from_wire(wire) is member


@pytest.mark.parametrize(("from_state", "to_state"), _ALLOWED_EDGES)
def test_every_allowed_edge_transitions_without_raising(
    from_state: WardenState, to_state: WardenState
) -> None:
    assert can_transition(from_state, to_state)
    assert_transition(from_state, to_state)  # never raises


def test_transitions_table_has_exactly_the_allowed_edges() -> None:
    actual = {(frm, to) for frm, tos in TRANSITIONS.items() for to in tos}
    assert actual == set(_ALLOWED_EDGES)


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (WardenState.STARTING, WardenState.STOPPED),  # must lease (or refuse) first
        (WardenState.STARTING, WardenState.OFFLINE),
        (WardenState.STOPPED, WardenState.ACTIVE),  # terminal
        (WardenState.STOPPED, WardenState.STARTING),
        (WardenState.ACTIVE, WardenState.STARTING),  # never returns to STARTING
        (WardenState.WATCH, WardenState.MIGRATING),  # only an ACTIVE Warden migrates
    ],
)
def test_forbidden_edges_raise(from_state: WardenState, to_state: WardenState) -> None:
    assert not can_transition(from_state, to_state)
    with pytest.raises(InvalidWardenTransitionError):
        assert_transition(from_state, to_state, warden_id="warden_test")


def test_is_terminal_is_true_only_for_stopped() -> None:
    for member in WardenState:
        assert is_terminal(member) == (member is WardenState.STOPPED)


def test_invalid_transition_error_names_both_states_and_the_warden_id() -> None:
    with pytest.raises(InvalidWardenTransitionError) as excinfo:
        assert_transition(WardenState.STOPPED, WardenState.ACTIVE, warden_id="warden_abc")
    message = str(excinfo.value)
    assert "STOPPED" in message
    assert "ACTIVE" in message
    assert "warden_abc" in message


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 4.9 (Clustering): settled_state, clustering_update
# ──────────────────────────────────────────────────────────────────────────────


def test_settled_state_is_watch_with_no_sub_bees() -> None:
    assert settled_state((), frozenset()) is WardenState.WATCH


def test_settled_state_is_active_when_no_sub_bee_is_clustered() -> None:
    task_id = make_assignment().task_id

    assert settled_state((task_id,), frozenset()) is WardenState.ACTIVE


def test_settled_state_is_clustered_when_every_sub_bee_is_clustered() -> None:
    task_id = make_assignment().task_id

    assert settled_state((task_id,), frozenset({task_id})) is WardenState.CLUSTERED


def test_settled_state_stays_clustered_once_every_paused_bee_has_been_retired() -> None:
    # A bee the Queen paused stops after its Handoff and its row is retired; the Warden holds
    # only the paused task, and CLUSTERED has no edge to WATCH (Appendix C): it waits on her.
    task_id = make_assignment().task_id

    assert settled_state((), frozenset({task_id})) is WardenState.CLUSTERED


def test_settled_state_is_active_when_only_some_sub_bees_are_clustered() -> None:
    clustered_id = make_assignment().task_id
    running_id = make_assignment().task_id

    target = settled_state((clustered_id, running_id), frozenset({clustered_id}))

    assert target is WardenState.ACTIVE


def test_settled_state_treats_a_none_task_id_as_never_clustered() -> None:
    # A sub-bee whose own task id somehow reads None (defensive: SubBee.task_id is always a real
    # TaskId in practice) never matches `clustered`, so it can never make the Warden CLUSTERED.
    assert settled_state((None,), frozenset()) is WardenState.ACTIVE


def test_clustering_update_adds_a_task_on_a_handoff_intervene() -> None:
    task_id = make_assignment().task_id
    payload = Intervene(
        action=InterventionAction.HANDOFF,
        subject=None,
        task_id=task_id,
        slot=None,
        alarm_id=None,
        reason="Clustering: provider unavailable.",
    )
    clustered: set[TaskId] = set()

    clustering_update(task_id, payload, clustered)

    assert clustered == {task_id}


def test_clustering_update_drops_a_task_on_task_resume() -> None:
    task_id = make_assignment().task_id
    clustered: set[TaskId] = {task_id}

    clustering_update(
        task_id,
        TaskResume(task_id=task_id, attempt=2, resume_from=None, slot=None, reason="resumed"),
        clustered,
    )

    assert clustered == set()


def test_clustering_update_ignores_a_none_task_id() -> None:
    payload = Intervene(
        action=InterventionAction.HANDOFF,
        subject=None,
        task_id=None,
        slot=None,
        alarm_id=None,
        reason="irrelevant",
    )
    clustered: set[TaskId] = set()

    clustering_update(None, payload, clustered)

    assert clustered == set()


def test_clustering_update_ignores_other_payload_kinds() -> None:
    task_id = make_assignment().task_id
    clustered: set[TaskId] = set()

    clustering_update(task_id, TaskCancel(task_id=task_id, grace_s=0.0, reason="x"), clustered)
    clustering_update(
        task_id,
        Intervene(
            action=InterventionAction.COMPACT,
            subject=None,
            task_id=task_id,
            slot=None,
            alarm_id=None,
            reason="x",
        ),
        clustered,
    )

    assert clustered == set()
