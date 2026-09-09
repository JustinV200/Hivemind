"""Tests for hivemind.brood_chamber.task.state: TaskStatus and TRANSITIONS.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/task/state.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.task.state for the module under test.
    - .claude/codingrules.md section 14.3: "Every state machine has a test that walks every
      allowed transition and asserts every forbidden one raises."
"""

from __future__ import annotations

import itertools

import pytest

from hivemind.brood_chamber.errors import InvalidTransitionError
from hivemind.brood_chamber.task.state import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    TaskStatus,
    assert_transition,
    can_transition,
    is_terminal,
)

# Every (from, to) pair TRANSITIONS allows; used to parametrize the "every edge succeeds" test and
# to build its complement below for the "every non-edge raises" test.
_ALLOWED_EDGES = [(status, target) for status, targets in TRANSITIONS.items() for target in targets]
_ALL_PAIRS = list(itertools.product(TaskStatus, TaskStatus))
_FORBIDDEN_EDGES = [pair for pair in _ALL_PAIRS if pair not in _ALLOWED_EDGES]


def test_transitions_has_exactly_one_entry_per_task_status() -> None:
    assert set(TRANSITIONS.keys()) == set(TaskStatus)


def test_terminal_statuses_matches_the_statuses_with_no_outgoing_edge() -> None:
    empty_edge_statuses = {status for status, targets in TRANSITIONS.items() if not targets}

    assert empty_edge_statuses == TERMINAL_STATUSES


@pytest.mark.parametrize(("from_status", "to_status"), _ALLOWED_EDGES)
def test_can_transition_accepts_every_allowed_edge(
    from_status: TaskStatus, to_status: TaskStatus
) -> None:
    assert can_transition(from_status, to_status) is True


@pytest.mark.parametrize(("from_status", "to_status"), _ALLOWED_EDGES)
def test_assert_transition_does_not_raise_on_every_allowed_edge(
    from_status: TaskStatus, to_status: TaskStatus
) -> None:
    assert_transition(from_status, to_status)


@pytest.mark.parametrize(("from_status", "to_status"), _FORBIDDEN_EDGES)
def test_can_transition_rejects_every_pair_outside_the_table(
    from_status: TaskStatus, to_status: TaskStatus
) -> None:
    assert can_transition(from_status, to_status) is False


@pytest.mark.parametrize(("from_status", "to_status"), _FORBIDDEN_EDGES)
def test_assert_transition_raises_on_every_pair_outside_the_table(
    from_status: TaskStatus, to_status: TaskStatus
) -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition(from_status, to_status)


def test_assert_transition_error_carries_the_task_id_when_given() -> None:
    with pytest.raises(InvalidTransitionError) as excinfo:
        assert_transition(TaskStatus.SUCCEEDED, TaskStatus.RUNNING, task_id="task_abc")

    assert excinfo.value.subject_id == "task_abc"


@pytest.mark.parametrize("status", list(TaskStatus))
def test_is_terminal_matches_terminal_statuses(status: TaskStatus) -> None:
    assert is_terminal(status) == (status in TERMINAL_STATUSES)


def test_every_terminal_status_has_no_allowed_transitions() -> None:
    for status in TERMINAL_STATUSES:
        assert TRANSITIONS[status] == frozenset()


def test_all_pairs_partition_into_allowed_and_forbidden_with_no_overlap() -> None:
    # Sanity check on the fixture-building logic above: every one of the 64 (status, status)
    # pairs is in exactly one of the two lists, so the two parametrized tests together cover the
    # full cartesian product with no gap and no double-count.
    assert len(_ALLOWED_EDGES) + len(_FORBIDDEN_EDGES) == len(_ALL_PAIRS)
    assert set(_ALLOWED_EDGES).isdisjoint(_FORBIDDEN_EDGES)
