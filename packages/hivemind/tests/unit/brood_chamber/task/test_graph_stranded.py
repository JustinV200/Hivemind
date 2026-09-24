"""Tests for hivemind.brood_chamber.task.graph.stranded_tasks: PENDING work that can never run.

Fits into the Hive:
    Mirrors src/hivemind/brood_chamber/task/graph.py (codingrules section 3), split by feature
    (14.2) from test_graph.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.brood_chamber.task.graph for the function under test.
    - tests/unit/queen/test_queen_results.py for the Queen sweep that acts on it.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber.task.graph import stranded_tasks
from hivemind.brood_chamber.task.state import TaskStatus
from waggle.clock import FakeClock


@pytest.mark.parametrize("dead_end", [TaskStatus.FAILED, TaskStatus.CANCELLED])
def test_a_pending_dependant_of_a_dead_end_is_stranded_with_its_cause(dead_end: TaskStatus) -> None:
    clock = FakeClock()
    root = make_task(dead_end, clock=clock)
    child = make_task(clock=clock, spec=make_task_spec(depends_on=(root.id,)))

    assert stranded_tasks((root, child)) == ((child, root.id),)


def test_the_whole_chain_below_a_dead_end_is_stranded_by_it() -> None:
    clock = FakeClock()
    root = make_task(TaskStatus.FAILED, clock=clock)
    # A second apart: the result is ordered by creation time, and ids minted in one instant
    # would order by their random part instead.
    clock.advance(1)
    child = make_task(clock=clock, spec=make_task_spec(depends_on=(root.id,)))
    clock.advance(1)
    grandchild = make_task(clock=clock, spec=make_task_spec(depends_on=(child.id,)))

    assert stranded_tasks((grandchild, child, root)) == ((child, root.id), (grandchild, root.id))


def test_nothing_is_stranded_while_every_dependency_can_still_succeed() -> None:
    clock = FakeClock()
    done = make_task(TaskStatus.SUCCEEDED, clock=clock)
    running = make_task(TaskStatus.RUNNING, clock=clock)
    waiting = make_task(clock=clock, spec=make_task_spec(depends_on=(done.id, running.id)))

    assert stranded_tasks((done, running, waiting)) == ()


def test_a_task_with_two_dead_ends_names_the_earliest_every_time() -> None:
    clock = FakeClock()
    first = make_task(TaskStatus.FAILED, clock=clock)
    clock.advance(1)
    second = make_task(TaskStatus.CANCELLED, clock=clock)
    both = make_task(clock=clock, spec=make_task_spec(depends_on=(second.id, first.id)))

    assert stranded_tasks((second, both, first)) == ((both, first.id),)


def test_a_dependant_already_terminal_is_left_alone() -> None:
    clock = FakeClock()
    root = make_task(TaskStatus.FAILED, clock=clock)
    already = make_task(
        TaskStatus.CANCELLED, clock=clock, spec=make_task_spec(depends_on=(root.id,))
    )

    assert stranded_tasks((root, already)) == ()
