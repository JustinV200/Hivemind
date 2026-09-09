"""Unit tests for hivemind.workers.errors: the error tree's shape and messages."""

from __future__ import annotations

from hivemind.common.errors import ConflictError, HiveMindError
from hivemind.workers.errors import (
    InvalidWorkerTransitionError,
    WorkerCancelledError,
    WorkerError,
)
from hivemind.workers.state import WorkerState


def test_worker_error_is_a_hivemind_error() -> None:
    assert issubclass(WorkerError, HiveMindError)


def test_invalid_worker_transition_error_is_a_conflict_error() -> None:
    assert issubclass(InvalidWorkerTransitionError, ConflictError)


def test_invalid_worker_transition_error_names_both_states_and_the_worker_id() -> None:
    error = InvalidWorkerTransitionError(
        WorkerState.DONE, WorkerState.RUNNING, worker_id="worker_x"
    )

    assert error.from_state is WorkerState.DONE
    assert error.to_state is WorkerState.RUNNING
    assert error.worker_id == "worker_x"
    assert "DONE" in str(error)
    assert "RUNNING" in str(error)
    assert "worker_x" in str(error)


def test_invalid_worker_transition_error_message_omits_worker_id_when_absent() -> None:
    error = InvalidWorkerTransitionError(WorkerState.DONE, WorkerState.RUNNING)

    assert error.worker_id is None
    assert "None" not in str(error)


def test_worker_cancelled_error_is_a_worker_error() -> None:
    assert issubclass(WorkerCancelledError, WorkerError)


def test_worker_cancelled_error_carries_its_reason() -> None:
    error = WorkerCancelledError("grace period elapsed")

    assert error.reason == "grace period elapsed"
    assert "grace period elapsed" in str(error)


def test_every_error_class_has_a_unique_code() -> None:
    codes = [WorkerError.code, InvalidWorkerTransitionError.code, WorkerCancelledError.code]

    assert len(codes) == len(set(codes))
