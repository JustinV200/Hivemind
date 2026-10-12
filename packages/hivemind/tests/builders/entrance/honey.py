"""Seed a Honey Store for the Honey browser tests: two scopes, a Cell's wax and Bee Bread.

``seed_honey`` opens a real SQLite Honey Store (``unit.honey_store.browse.harness``) and fills it
the way the Hive would have: a shared C1 finding and a shared C2 note the human proposed, one row
in each of two Cells' scopes (the second a C2 row), one row in a task's scope, a live Cell Wax note
on the first Cell, and a Bee Bread entry about the task. ``Seeded.browser`` is the real
``HoneyBrowser`` over it, with a full-text retriever, ready to hand to the rig as
``RigOptions(honey=...)``; the ids and scopes a test asserts on are fields.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance that drive the Honey browser over real sockets.

Key invariants:
    - Every row is ripened as the Ripener stores it; nothing is faked but the wax and Bee Bread
      sources, which are the shipped in-memory ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from unit.honey_store.browse.harness import BrowseHive, open_browse_hive

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse import BeeBreadNote, HoneyBrowser, WaxNote
from hivemind.honey_store.models import Honey, NectarOrigin
from hivemind.honey_store.scope import cell_scope, task_scope
from waggle.ids import CellId, TaskId, new_cell_id, new_task_id
from waggle.messages.cell.wax import WaxSeverity

__all__ = ["WORDS", "Seeded", "seed_honey"]

WORDS = "widget factory staging config"  # What every searchable row says, so a search hits all.


@dataclass(frozen=True, slots=True)
class Seeded:
    """A seeded Honey Store, and the ids and rows a test asserts on."""

    hive: BrowseHive
    browser: HoneyBrowser
    cell: CellId  # The first Cell: one C1 row and a live wax note.
    other_cell: CellId  # The second Cell: one C2 row.
    task: TaskId
    shared: Honey  # hive scope, C1.
    shared_note: Honey  # hive scope, C2, ripened from the human's own note.
    cell_row: Honey  # cell scope of `cell`, C1.
    other_cell_row: Honey  # cell scope of `other_cell`, C2.
    task_row: Honey  # task scope, C1.
    wax: WaxNote  # `cell`'s live CAUTION, C1, with an expiry.
    bread: BeeBreadNote  # An entry about `task`, so it is filed under the task's scope.


async def seed_honey(tmp_path: Path) -> Seeded:
    """Open a temp-file Honey Store and seed it as the module docstring says.

    Args:
        tmp_path: A writable directory for the store's file.

    Returns:
        The seeded store, its browser and the ids a test asserts on.
    """
    hive = await open_browse_hive(tmp_path)
    cell, other_cell, task = (
        new_cell_id(hive.clock),
        new_cell_id(hive.clock),
        new_task_id(hive.clock),
    )
    (shared,) = await hive.ripen(f"Shared: the {WORDS} lives in /etc/widgets.")
    (shared_note,) = await hive.ripen(
        f"Note: the {WORDS} password is rotated monthly.",
        clearance=HoneyClearance.C2,
        origin=NectarOrigin.HUMAN,
    )
    (cell_row,) = await hive.ripen(f"Cell: the {WORDS} drifts.", scope=cell_scope(cell))
    (other_cell_row,) = await hive.ripen(
        f"Other cell: the {WORDS} holds the owner's address.",
        scope=cell_scope(other_cell),
        clearance=HoneyClearance.C2,
    )
    (task_row,) = await hive.ripen(f"Task: the {WORDS} was copied.", scope=task_scope(task))
    wax = hive.wax_note(
        cell, severity=WaxSeverity.CAUTION, expires_at=hive.clock.now().replace(year=2099)
    )
    bread = hive.bee_bread_note(task_id=task, payload="make build\nok\n")
    browser = hive.browser(wax=[wax], bee_bread=[bread], searchable=True)
    return Seeded(
        hive=hive,
        browser=browser,
        cell=cell,
        other_cell=other_cell,
        task=task,
        shared=shared,
        shared_note=shared_note,
        cell_row=cell_row,
        other_cell_row=other_cell_row,
        task_row=task_row,
        wax=wax,
        bread=bread,
    )
