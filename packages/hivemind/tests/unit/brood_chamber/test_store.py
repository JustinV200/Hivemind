"""Tests for hivemind.brood_chamber.store: TaskFilter's bounds and check_task_event.

TaskStore's own behavioural contract is covered by tests/contracts/test_task_store_contract.py,
parametrised over MemoryTaskStore and SqliteTaskStore. This module covers what belongs to
store.py itself: TaskFilter's field bounds, and check_task_event, the guard both implementations
call before every write.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/store.py (codingrules section 3: tests/unit mirrors src/
    one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.store for the module under test.
    - tests/contracts/test_task_store_contract.py for the shared TaskStore behaviour.
"""

from __future__ import annotations

from typing import cast

import pytest
from builders.tasks import make_task
from pydantic import ValidationError

from hivemind.brood_chamber.store import (
    DEFAULT_TASK_FILTER_LIMIT,
    MAX_TASK_FILTER_LIMIT,
    MIN_TASK_FILTER_LIMIT,
    TaskFilter,
    check_task_event,
)
from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone.events import CellEvent, TaskEvent
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_task_id


def _make_task_event(clock: FakeClock, subject_id: str, kind: str = "task.submitted") -> TaskEvent:
    """Build a well-formed TaskEvent naming `subject_id`, minting a fresh id and node."""
    return TaskEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=subject_id,
        payload={},
    )


# ──────────────────────────────────────────────────────────────────────────────
# TaskFilter
# ──────────────────────────────────────────────────────────────────────────────


def test_task_filter_defaults_to_no_filters_and_the_default_limit() -> None:
    query = TaskFilter()

    assert query.status is None
    assert query.goal_id is None
    assert query.limit == DEFAULT_TASK_FILTER_LIMIT


def test_task_filter_accepts_limit_at_the_minimum_and_maximum_bounds() -> None:
    lower = TaskFilter(limit=MIN_TASK_FILTER_LIMIT)
    upper = TaskFilter(limit=MAX_TASK_FILTER_LIMIT)

    assert lower.limit == MIN_TASK_FILTER_LIMIT
    assert upper.limit == MAX_TASK_FILTER_LIMIT


def test_task_filter_rejects_a_limit_below_the_minimum() -> None:
    with pytest.raises(ValidationError):
        TaskFilter(limit=MIN_TASK_FILTER_LIMIT - 1)


def test_task_filter_rejects_a_limit_above_the_maximum() -> None:
    with pytest.raises(ValidationError):
        TaskFilter(limit=MAX_TASK_FILTER_LIMIT + 1)


def test_task_filter_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        TaskFilter(unknown="nope")  # type: ignore[call-arg]


# ──────────────────────────────────────────────────────────────────────────────
# check_task_event
# ──────────────────────────────────────────────────────────────────────────────


def test_check_task_event_accepts_a_matching_pair() -> None:
    clock = FakeClock()
    task = make_task(clock=clock)
    event = _make_task_event(clock, task.id)

    check_task_event(task, event)  # does not raise


def test_check_task_event_rejects_a_mismatched_subject_id() -> None:
    clock = FakeClock()
    task = make_task(clock=clock)
    event = _make_task_event(clock, new_task_id(clock))

    with pytest.raises(InvariantViolationError):
        check_task_event(task, event)


def test_check_task_event_rejects_a_mismatched_family() -> None:
    clock = FakeClock()
    task = make_task(clock=clock)
    # A CellEvent whose subject_id happens to be the task's own id: subject_id accepts any
    # waggle IdKind prefix (hivemind.pheromone.events.base), so this is well-formed on its own and
    # lets this test exercise check_task_event's family check in isolation from its subject_id
    # check. cast() only tells mypy what the test already knows at runtime: the guard must still
    # catch a wrong-family event even though the type system rules this out at a real call site.
    cell_event = CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="cell.provisioned",
        subject_id=task.id,
        payload={},
    )

    with pytest.raises(InvariantViolationError):
        check_task_event(task, cast(TaskEvent, cell_event))
