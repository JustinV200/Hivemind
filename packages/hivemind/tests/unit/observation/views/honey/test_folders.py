"""Test hivemind.observation.views.honey.folders: a folder's page as the browser filtered it.

Over a real SQLite Honey Store and the shipped fake sources: the root and a Cell's folder show
sub-folders and items apart, with their parent, scope, whether they can be searched and the
viewer's ceiling; a truncated page carries a cursor that names where the next begins.

Fits into the Hive:
    Mirrors src/hivemind/observation/views/honey/folders.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive, reader

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse import Page
from hivemind.honey_store.scope import cell_scope
from hivemind.observation.views.honey.folders import HoneyFolderView
from hivemind.observation.views.honey.queries import decode_honey_cursor
from waggle.ids import new_cell_id


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


async def test_the_root_is_five_sub_folders_with_no_parent_and_no_items(hive: BrowseHive) -> None:
    listing = await hive.browser().ls("/", reader())

    view = HoneyFolderView.of_listing(listing, HoneyClearance.C1, 0, 50)

    assert [folder.name for folder in view.folders] == [
        "hive",
        "cells",
        "bees",
        "tasks",
        "bee-bread",
    ]
    assert view.items == [] and view.next_cursor is None
    assert (view.path, view.parent, view.scope, view.can_search) == ("/", None, None, True)
    assert view.ceiling is HoneyClearance.C1


async def test_a_cells_folder_lists_its_wax_folder_apart_from_its_rows(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)
    (row,) = await hive.ripen("Cell history.", scope=cell_scope(cell))
    browser = hive.browser(wax=[hive.wax_note(cell)])

    cell_view = HoneyFolderView.of_listing(
        await browser.ls(f"/cells/{cell}", reader()), HoneyClearance.C2, 0, 50
    )
    wax_view = HoneyFolderView.of_listing(
        await browser.ls(f"/cells/{cell}/wax", reader()), HoneyClearance.C2, 0, 50
    )

    assert [folder.path for folder in cell_view.folders] == [f"/cells/{cell}/wax"]
    assert [item.id for item in cell_view.items] == [row.id]
    assert (cell_view.parent, cell_view.scope, cell_view.can_search) == (
        "/cells",
        cell_scope(cell),
        True,
    )
    assert (wax_view.parent, wax_view.scope, wax_view.can_search) == (
        f"/cells/{cell}",
        cell_scope(cell),
        False,
    )
    assert [item.kind.value for item in wax_view.items] == ["wax"]


async def test_a_truncated_page_names_where_the_next_one_starts(hive: BrowseHive) -> None:
    for number in range(5):
        await hive.ripen(f"Row {number}.")
    browser = hive.browser()

    first = HoneyFolderView.of_listing(
        await browser.ls("/hive", reader(), Page(limit=2, offset=0)), HoneyClearance.C1, 0, 2
    )
    last = HoneyFolderView.of_listing(
        await browser.ls("/hive", reader(), Page(limit=2, offset=4)), HoneyClearance.C1, 4, 2
    )

    assert len(first.items) == 2 and first.next_cursor is not None
    assert decode_honey_cursor(first.next_cursor) == 2
    assert len(last.items) == 1 and last.next_cursor is None
    assert last.parent == "/" and last.scope == "hive"
