"""Tests for hivemind.brood_chamber.errors: BroodChamberError and its subclasses.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/errors.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.errors for the module under test.
"""

from __future__ import annotations

from hivemind.brood_chamber.errors import (
    BroodChamberError,
    InvalidGraphError,
    InvalidTransitionError,
    QuestionNotFoundError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
)
from hivemind.brood_chamber.task_state import TaskStatus
from hivemind.common.errors import ConflictError, HiveMindError, NotFoundError


def test_brood_chamber_error_has_its_own_root_code() -> None:
    error = BroodChamberError("a full sentence message.")

    assert isinstance(error, HiveMindError)
    assert error.code == "hivemind.brood_chamber.error"
    assert str(error) == "a full sentence message."


def test_task_not_found_error_carries_the_id_and_is_a_not_found_error() -> None:
    error = TaskNotFoundError("task_01ARZ3NDEKTSV4RRFFQ69G5FAV")

    # NotFoundError, not BroodChamberError: this class subclasses the common category directly
    # (errors.py's own docstring explains why), so HiveMindError is the only shared root.
    assert isinstance(error, NotFoundError)
    assert isinstance(error, HiveMindError)
    assert error.code == "hivemind.brood_chamber.task_not_found"
    assert error.task_id == "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert "task_01ARZ3NDEKTSV4RRFFQ69G5FAV" in str(error)


def test_question_not_found_error_carries_the_id_and_is_a_not_found_error() -> None:
    error = QuestionNotFoundError("msg_01ARZ3NDEKTSV4RRFFQ69G5FAV")

    assert isinstance(error, NotFoundError)
    assert isinstance(error, HiveMindError)
    assert error.code == "hivemind.brood_chamber.question_not_found"
    assert error.question_id == "msg_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert "msg_01ARZ3NDEKTSV4RRFFQ69G5FAV" in str(error)


def test_task_already_exists_error_carries_the_id_and_is_a_conflict_error() -> None:
    error = TaskAlreadyExistsError("task_01ARZ3NDEKTSV4RRFFQ69G5FAV")

    assert isinstance(error, ConflictError)
    assert isinstance(error, HiveMindError)
    assert error.code == "hivemind.brood_chamber.task_exists"
    assert error.task_id == "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert "task_01ARZ3NDEKTSV4RRFFQ69G5FAV" in str(error)


def test_invalid_transition_error_message_names_both_statuses_without_a_subject_id() -> None:
    error = InvalidTransitionError(TaskStatus.SUCCEEDED, TaskStatus.RUNNING)

    assert isinstance(error, ConflictError)
    assert isinstance(error, HiveMindError)
    assert error.code == "hivemind.brood_chamber.invalid_transition"
    assert error.from_status is TaskStatus.SUCCEEDED
    assert error.to_status is TaskStatus.RUNNING
    assert error.subject_id is None
    assert "SUCCEEDED" in str(error)
    assert "RUNNING" in str(error)


def test_invalid_transition_error_message_includes_the_subject_id_when_given() -> None:
    error = InvalidTransitionError(
        TaskStatus.PENDING, TaskStatus.RUNNING, subject_id="task_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    )

    assert error.subject_id == "task_01ARZ3NDEKTSV4RRFFQ69G5FAV"
    assert "task_01ARZ3NDEKTSV4RRFFQ69G5FAV" in str(error)


def test_invalid_graph_error_has_its_own_code() -> None:
    error = InvalidGraphError("TaskGraphDraft.tasks contains a dependency cycle.")

    assert isinstance(error, BroodChamberError)
    assert error.code == "hivemind.brood_chamber.invalid_graph"


def test_brood_chamber_errors_each_have_a_distinct_code() -> None:
    codes = [
        BroodChamberError.code,
        TaskNotFoundError.code,
        QuestionNotFoundError.code,
        TaskAlreadyExistsError.code,
        InvalidTransitionError.code,
        InvalidGraphError.code,
    ]

    assert len(codes) == len(set(codes))
