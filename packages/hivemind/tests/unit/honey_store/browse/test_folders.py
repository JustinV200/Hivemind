"""Tests for hivemind.honey_store.browse.folders: `ls` for every folder, filtered like retrieval.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/folders.py (codingrules section 3). Runs over a
    real SQLite Honey Store with the shipped fake wax and Bee Bread sources (`harness`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.folders for the module under test.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive, reader

from hivemind.cell import HoneyClearance
from hivemind.guard import CapabilitySet
from hivemind.honey_store.browse import folders
from hivemind.honey_store.browse.errors import BrowseInputError, BrowsePathError
from hivemind.honey_store.browse.folders import Page, search_scopes
from hivemind.honey_store.browse.listing import EntryKind
from hivemind.honey_store.browse.paths import parse_path
from hivemind.honey_store.scope import cell_scope, task_scope
from waggle.ids import new_cell_id, new_task_id


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


async def test_ls_root_lists_the_five_top_level_folders(hive: BrowseHive) -> None:
    listing = await hive.browser().ls("/", reader())

    assert [entry.path for entry in listing.entries] == [
        "/hive",
        "/cells",
        "/bees",
        "/tasks",
        "/bee-bread",
    ]
    assert all(entry.kind is EntryKind.FOLDER for entry in listing.entries)


async def test_ls_hive_lists_its_rows_newest_first_with_label_and_provenance(
    hive: BrowseHive,
) -> None:
    (older,) = await hive.ripen("First finding.")
    hive.clock.advance(1)
    (newer,) = await hive.ripen("Second finding.", clearance=HoneyClearance.C0)
    await hive.ripen("Task material.", scope=task_scope(new_task_id(hive.clock)))

    listing = await hive.browser().ls("/hive", reader())

    assert [entry.path for entry in listing.entries] == [newer.path, older.path]
    assert [entry.clearance for entry in listing.entries] == [HoneyClearance.C0, HoneyClearance.C1]
    assert all(entry.provenance is not None for entry in listing.entries)
    assert not listing.is_truncated


async def test_ls_filters_rows_by_the_ceiling_and_the_capabilities(hive: BrowseHive) -> None:
    (public,) = await hive.ripen("Public fact.", clearance=HoneyClearance.C0)
    await hive.ripen("Royal fact.", clearance=HoneyClearance.C2)
    browser = hive.browser()

    capped = await browser.ls("/hive", reader(ceiling=HoneyClearance.C1))
    unreadable = await browser.ls("/hive", reader(CapabilitySet.parse("honey:read:task:*")))

    assert [entry.path for entry in capped.entries] == [public.path]
    assert unreadable.entries == ()


async def test_ls_pages_a_folder_and_says_when_more_exist(hive: BrowseHive) -> None:
    for index in range(3):
        await hive.ripen(f"Finding number {index}.")
        hive.clock.advance(1)
    browser = hive.browser()

    first = await browser.ls("/hive", reader(), Page(limit=2))
    second = await browser.ls("/hive", reader(), Page(limit=2, offset=2))

    assert (len(first.entries), first.is_truncated) == (2, True)
    assert (len(second.entries), second.is_truncated) == (1, False)


async def test_ls_tasks_lists_each_task_scope_holding_visible_rows(hive: BrowseHive) -> None:
    visible_task = new_task_id(hive.clock)
    royal_task = new_task_id(hive.clock)
    await hive.ripen("One.", scope=task_scope(visible_task))
    await hive.ripen("Two.", scope=task_scope(visible_task))
    await hive.ripen("Royal.", scope=task_scope(royal_task), clearance=HoneyClearance.C2)

    listing = await hive.browser().ls("/tasks", reader(ceiling=HoneyClearance.C1))

    assert [(entry.path, entry.detail) for entry in listing.entries] == [
        (f"/tasks/{visible_task}", "2 rows")
    ]


async def test_ls_cells_lists_cells_with_honey_and_cells_with_only_live_wax(
    hive: BrowseHive,
) -> None:
    with_honey = new_cell_id(hive.clock)
    with_wax = new_cell_id(hive.clock)
    only_expired = new_cell_id(hive.clock)
    await hive.ripen("Cell history.", scope=cell_scope(with_honey))
    wax = (
        hive.wax_note(with_wax),
        hive.wax_note(only_expired, expires_at=hive.clock.now() - timedelta(seconds=1)),
    )

    listing = await hive.browser(wax=wax).ls("/cells", reader())

    details = {entry.path: entry.detail for entry in listing.entries}
    assert details == {f"/cells/{with_honey}": "1 rows", f"/cells/{with_wax}": "1 wax"}


async def test_ls_index_says_when_its_scan_stopped_at_the_bound(
    hive: BrowseHive, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(3):
        await hive.ripen("Task row.", scope=task_scope(new_task_id(hive.clock)))
    monkeypatch.setattr(folders, "SCAN_PAGE_ROWS", 1)
    monkeypatch.setattr(folders, "MAX_SCAN_ROWS", 2)

    listing = await hive.browser().ls("/tasks", reader())

    assert len(listing.entries) == 2
    assert listing.is_truncated
    assert "2" in listing.note


async def test_ls_a_cell_folder_offers_its_wax_folder_first(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)
    (row,) = await hive.ripen("Cell history.", scope=cell_scope(cell))

    listing = await hive.browser().ls(f"/cells/{cell}", reader())

    assert [entry.path for entry in listing.entries] == [f"/cells/{cell}/wax", row.path]
    assert listing.entries[0].kind is EntryKind.FOLDER


async def test_ls_wax_lists_live_visible_notes_newest_first(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)
    older = hive.wax_note(cell)
    hive.clock.advance(1)
    newer = hive.wax_note(cell)
    royal = hive.wax_note(cell, clearance=HoneyClearance.C2)
    expired = hive.wax_note(cell, expires_at=hive.clock.now() - timedelta(seconds=1))
    browser = hive.browser(wax=(older, newer, royal, expired))

    listing = await browser.ls(f"/cells/{cell}/wax", reader(ceiling=HoneyClearance.C1))
    hidden = await browser.ls(f"/cells/{cell}/wax", reader(CapabilitySet.parse("honey:read:hive")))

    assert [entry.path.rsplit("/", 1)[1] for entry in listing.entries] == [newer.id, older.id]
    assert all(entry.kind is EntryKind.WAX for entry in listing.entries)
    assert hidden.entries == ()


async def test_ls_bee_bread_lists_recent_visible_entries_newest_first(hive: BrowseHive) -> None:
    task = new_task_id(hive.clock)
    old = hive.bee_bread_note(created_at=hive.clock.now() - timedelta(days=30))
    first = hive.bee_bread_note(task_id=task)
    hive.clock.advance(1)
    second = hive.bee_bread_note()
    browser = hive.browser(bee_bread=(old, first, second))

    listing = await browser.ls("/bee-bread", reader())
    hive_only = await browser.ls("/bee-bread", reader(CapabilitySet.parse("honey:read:hive")))

    assert [entry.path for entry in listing.entries] == [
        f"/bee-bread/{second.id}",
        f"/bee-bread/{first.id}",
    ]
    assert [entry.path for entry in hive_only.entries] == [f"/bee-bread/{second.id}"]


async def test_ls_a_document_path_lists_that_one_document(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("One row.")

    listing = await hive.browser().ls(row.path, reader())

    assert [entry.path for entry in listing.entries] == [row.path]


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (folders.MAX_PAGE_ROWS + 1, 0), (1, -1)])
def test_page_refuses_a_page_no_listing_serves(limit: int, offset: int) -> None:
    with pytest.raises(BrowseInputError):
        Page(limit=limit, offset=offset)


async def test_search_scopes_follow_the_folder(hive: BrowseHive) -> None:
    task = new_task_id(hive.clock)
    await hive.ripen("Task row.", scope=task_scope(task))
    deps = hive.deps()

    assert await search_scopes(deps, parse_path("/"), reader()) == ()
    assert await search_scopes(deps, parse_path("/hive"), reader()) == ("hive",)
    assert await search_scopes(deps, parse_path("/tasks"), reader()) == (task_scope(task),)
    assert await search_scopes(deps, parse_path("/bees"), reader()) is None
    for path in ("/bee-bread", f"/cells/{new_cell_id(hive.clock)}/wax", f"/tasks/{task}/x"):
        with pytest.raises(BrowsePathError):
            await search_scopes(deps, parse_path(path), reader())


async def test_ls_bee_bread_says_when_its_fetch_hit_the_bound(
    hive: BrowseHive, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = (hive.bee_bread_note(), hive.bee_bread_note())
    monkeypatch.setattr(folders, "MAX_BEE_BREAD_ROWS", 1)

    listing = await hive.browser(bee_bread=entries).ls("/bee-bread", reader())

    assert len(listing.entries) == 1
    assert listing.is_truncated
    assert "newest 1" in listing.note


async def test_ls_a_wax_note_or_bee_bread_path_lists_that_one_item(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)
    note = hive.wax_note(cell)
    entry = hive.bee_bread_note()
    browser = hive.browser(wax=(note,), bee_bread=(entry,))

    wax = await browser.ls(f"/cells/{cell}/wax/{note.id}", reader())
    bread = await browser.ls(f"/bee-bread/{entry.id}", reader())

    assert [(item.kind, item.path) for item in wax.entries] == [
        (EntryKind.WAX, f"/cells/{cell}/wax/{note.id}")
    ]
    assert [(item.kind, item.path) for item in bread.entries] == [
        (EntryKind.BEE_BREAD, f"/bee-bread/{entry.id}")
    ]
