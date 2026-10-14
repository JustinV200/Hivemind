"""Define survival_cells: which devices have a task that must outlive the link to the Queen.

A task says in its needs (`TaskNeeds.survives_link_loss`) that it must keep going when the link to
the Queen is lost, which is what a Warden on the device does (Level 1: it keeps its sub-bees warm)
and what a model server on it adds (Level 2: they keep thinking). The Queen reads that flag for
every unfinished task that is placed on a device, and for every one that is pinned to a device but
not placed yet, since colonizing the device only once the task is running on it would leave the task
without that survival for the first stretch. A task is pinned to a device when the capability set
of its goal (the ceiling the goal's submitter set, `Task.spec.capabilities`) lets it be placed on
one named Real Cell and on no other kind of Cell: placement excludes every Cell the goal does not
allow (`cell:real:<cell id>`, `cell:hive_stand`, `cell:virtual`), so a goal that allows exactly one
Real Cell and nothing else can run nowhere but there. Pure functions of tasks, so which devices want
survival is a table to test.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.rungs`. Called by `.readings` with the Queen's unfinished tasks.
    Calls into `hivemind.brood_chamber` (the task type), `hivemind.guard` (the capability
    grammar) and waggle only.

Key invariants:
    - Pure: no I/O and no clock; the same tasks give the same cells.
    - A task that does not set the flag asks nothing of any device, whatever its ceiling says.
    - A goal ceiling is pinned to a device only when it allows exactly one source of Cells, a
      named Real Cell: a wildcard, the Hive Stand or a Virtual Cell beside it leaves the task free
      to run elsewhere, so it asks nothing of this device.
    - A capability string that does not parse pins nothing; it is not this module's to reject.

See Also:
    - hivemind.cell.needs for `TaskNeeds.survives_link_loss`.
    - hivemind.queen.placement.rules for how a goal ceiling excludes the Cells it does not allow.
"""

from __future__ import annotations

from collections.abc import Iterable

from hivemind.brood_chamber import Task
from hivemind.guard import CapabilityFamily, CapabilitySet, InvalidCapabilityError
from waggle.ids import CellId

# The capability families that name where a task may be placed: one kind of Cell each.
_CELL_SOURCES = frozenset(
    {
        CapabilityFamily.CELL_REAL,
        CapabilityFamily.CELL_HIVE_STAND,
        CapabilityFamily.CELL_VIRTUAL,
    }
)

__all__ = ["pinned_cell", "survival_cells"]


def pinned_cell(task: Task) -> CellId | None:
    """Return the one Real Cell the task's goal ceiling lets it be placed on, or None.

    Args:
        task: An unfinished task.

    Returns:
        The Cell's id when the goal's capability set names exactly one source of Cells and it is a
        single, named Real Cell; None for a task with no ceiling, one that allows any Real Cell, the
        Hive Stand or a Virtual Cell, or one that allows several of these.
    """
    strings = task.spec.capabilities
    if not strings:
        return None
    try:
        held = CapabilitySet.parse(*strings)
    except InvalidCapabilityError:
        return None
    sources = [capability for capability in held.capabilities if capability.family in _CELL_SOURCES]
    if len(sources) != 1 or sources[0].family is not CapabilityFamily.CELL_REAL:
        return None
    scope = sources[0].scope
    return None if scope.endswith("*") else CellId(scope)  # A trailing `*` covers many Cells.


def survival_cells(tasks: Iterable[Task]) -> frozenset[CellId]:
    """Return every Cell a task that must survive a lost link is placed on, or pinned to.

    Args:
        tasks: The unfinished tasks of the Brood Chamber.

    Returns:
        The Cells a task that sets `survives_link_loss` is placed on or pinned to.
    """
    cells: set[CellId] = set()
    for task in tasks:
        if not task.spec.needs.survives_link_loss:
            continue
        if task.cell_id is not None:
            cells.add(task.cell_id)
        pinned = pinned_cell(task)
        if pinned is not None:
            cells.add(pinned)
    return frozenset(cells)
