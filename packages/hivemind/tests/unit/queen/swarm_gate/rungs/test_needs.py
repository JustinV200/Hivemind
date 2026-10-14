"""Tests for hivemind.queen.swarm_gate.rungs.needs: which devices have a task that must survive.

A task that sets `survives_link_loss` asks for a Warden on its device (and a model server on it) to
be there before the link is lost, so the Queen reads the flag for every unfinished task that is
placed on a device and every one pinned to a device. A goal's ceiling pins a task when it lets the
task be placed on exactly one named Real Cell and on no other kind of Cell. These tests hold both
to tables.

Fits into the Hive:
    Mirrors src/hivemind/queen/swarm_gate/rungs/needs.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.swarm_gate.rungs.needs for the module under test.
"""

from __future__ import annotations

import pytest
from builders.tasks import make_task, make_task_spec

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import TaskNeeds
from hivemind.queen.swarm_gate.rungs import pinned_cell, survival_cells
from waggle.clock import FakeClock
from waggle.ids import new_cell_id

_CLOCK = FakeClock()
_HERE = new_cell_id(_CLOCK)
_ELSEWHERE = new_cell_id(_CLOCK)


def _task(
    *capabilities: str,
    survives: bool = True,
    status: TaskStatus = TaskStatus.PENDING,
    **overrides: object,
) -> Task:
    """A task that does (or does not) need to survive a lost link, with a goal ceiling."""
    spec = make_task_spec(
        needs=TaskNeeds(survives_link_loss=survives),
        capabilities=tuple(sorted(capabilities)) or None,
    )
    return make_task(status=status, clock=_CLOCK, spec=spec, **overrides)


@pytest.mark.parametrize(
    ("capabilities", "expected"),
    [
        ((f"cell:real:{_HERE}",), _HERE),
        ((f"cell:real:{_HERE}", "cell:comb_shield:meadow"), _HERE),  # Other families do not matter.
        ((), None),  # No ceiling: the operator's own local submission.
        (("cell:comb_shield:meadow",), None),  # A ceiling that names no Cell at all.
        ((f"cell:real:{_HERE}", f"cell:real:{_ELSEWHERE}"), None),  # Two devices: free to choose.
        ((f"cell:real:{_HERE}", "cell:hive_stand"), None),  # Or the Hive Stand.
        ((f"cell:real:{_HERE}", "cell:virtual"), None),  # Or a Virtual Cell.
        (("cell:real:*",), None),  # Any Real Cell.
        (("cell:hive_stand",), None),
        (("cell:virtual",), None),
    ],
)
def test_a_task_is_pinned_to_a_device_only_by_a_ceiling_that_allows_no_other_cell(
    capabilities: tuple[str, ...], expected: str | None
) -> None:
    assert pinned_cell(_task(*capabilities)) == expected


def test_a_ceiling_that_does_not_parse_pins_nothing() -> None:
    task = _task(f"cell:real:{_HERE}")
    broken = task.model_copy(update={"spec": task.spec.model_copy(update={"capabilities": ("?",)})})

    assert pinned_cell(broken) is None


def test_a_task_placed_on_a_device_asks_survival_of_it() -> None:
    placed = _task(status=TaskStatus.RUNNING, cell_id=_HERE)

    assert survival_cells([placed]) == {_HERE}


def test_a_task_pinned_to_a_device_asks_survival_of_it_before_it_is_placed() -> None:
    pinned = _task(f"cell:real:{_HERE}")

    assert survival_cells([pinned]) == {_HERE}


def test_a_task_that_does_not_set_the_flag_asks_nothing_whatever_its_place_or_ceiling() -> None:
    quiet = _task(f"cell:real:{_HERE}", survives=False, status=TaskStatus.RUNNING, cell_id=_HERE)

    assert survival_cells([quiet]) == frozenset()


def test_a_task_placed_nowhere_and_pinned_nowhere_asks_nothing_of_any_device() -> None:
    assert survival_cells([_task(), _task("cell:hive_stand")]) == frozenset()


def test_the_cells_of_several_tasks_are_all_collected_once() -> None:
    tasks = [
        _task(status=TaskStatus.RUNNING, cell_id=_HERE),
        _task(status=TaskStatus.ASSIGNED, cell_id=_HERE),
        _task(f"cell:real:{_ELSEWHERE}"),
        _task(survives=False, status=TaskStatus.RUNNING, cell_id=new_cell_id(_CLOCK)),
    ]

    assert survival_cells(tasks) == {_HERE, _ELSEWHERE}


def test_a_task_placed_on_one_cell_and_pinned_to_another_asks_both() -> None:
    odd = _task(f"cell:real:{_ELSEWHERE}", status=TaskStatus.RUNNING, cell_id=_HERE)

    assert survival_cells([odd]) == {_HERE, _ELSEWHERE}
