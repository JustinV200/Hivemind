"""Tests for hivemind.honey_store.browse.documents: `cat`, and who may see which item.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/documents.py (codingrules section 3). Runs over a
    real SQLite Honey Store with the shipped fake wax and Bee Bread sources (`harness`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.documents for the module under test.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from builders.honey import make_honey_draft, make_nectar_draft
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive, reader

from hivemind.cell import HoneyClearance
from hivemind.guard import CapabilitySet
from hivemind.honey_store import honey_event
from hivemind.honey_store.browse.documents import (
    HoneyDocument,
    bee_bread_scope,
    bee_bread_visible,
    honey_visible,
    wax_visible,
)
from hivemind.honey_store.browse.errors import BrowseNotFoundError, BrowsePathError
from hivemind.honey_store.browse.sources import BeeBreadNote, WaxNote
from hivemind.honey_store.scope import cell_scope, task_scope
from waggle.ids import new_cell_id, new_task_id


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


async def test_cat_returns_a_visible_honey_row_with_its_extra_sources(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("The staging config lives at /etc/widgets.")

    document = await hive.browser().cat(row.path, reader())

    # A HoneyDocument is a Honey (ADR-0033) with the row's own fields unchanged, plus its Nectar's
    # extra sources -- none here, since nothing deduplicated onto this fresh row.
    assert document == HoneyDocument(**row.model_dump(), sources=())


async def test_cat_lists_a_honey_rows_extra_sources(hive: BrowseHive) -> None:
    draft = make_nectar_draft(clock=hive.clock, content=b"shared content")
    digest = hashlib.sha256(draft.content).hexdigest()
    added = await hive.store.add_nectar(draft, digest, lambda _added: ())
    event = honey_event(hive.identity, hive.clock, "honey.ripened", added.nectar.id)
    (row,) = await hive.store.ripen(added.nectar.id, (make_honey_draft(),), event)
    second_task = new_task_id(hive.clock)
    duplicate = draft.model_copy(update={"task_id": second_task, "source_key": "dup"})
    await hive.store.add_nectar(duplicate, digest, lambda _added: ())

    document = await hive.browser().cat(row.path, reader())

    assert isinstance(document, HoneyDocument)
    assert [source.task_id for source in document.sources] == [second_task]


async def test_cat_hides_a_row_above_the_ceiling_like_a_missing_one(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("A personal detail.", clearance=HoneyClearance.C2)
    browser = hive.browser()

    with pytest.raises(BrowseNotFoundError) as hidden:
        await browser.cat(row.path, reader(ceiling=HoneyClearance.C1))
    with pytest.raises(BrowseNotFoundError) as missing:
        await browser.cat("/hive/honey_01ARZ3NDEKTSV4RRFFQ69G5FAV", reader())

    assert str(hidden.value).replace(row.id, "X") == str(missing.value).replace(
        "honey_01ARZ3NDEKTSV4RRFFQ69G5FAV", "X"
    )


async def test_cat_hides_a_row_whose_scope_the_reader_cannot_read(hive: BrowseHive) -> None:
    task = new_task_id(hive.clock)
    (row,) = await hive.ripen("Working notes.", scope=task_scope(task))
    only_hive = CapabilitySet.parse("honey:read:hive")

    with pytest.raises(BrowseNotFoundError):
        await hive.browser().cat(row.path, reader(only_hive))


async def test_cat_refuses_a_row_reached_through_another_scopes_folder(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("Shared knowledge.")
    task = new_task_id(hive.clock)

    with pytest.raises(BrowseNotFoundError):
        await hive.browser().cat(f"/tasks/{task}/{row.id}", reader())


async def test_cat_hides_a_retired_row(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("Superseded knowledge.")
    await hive.store.retire(row.id, honey_event(hive.identity, hive.clock, "honey.retired", row.id))

    with pytest.raises(BrowseNotFoundError):
        await hive.browser().cat(row.path, reader())


async def test_cat_refuses_a_folder(hive: BrowseHive) -> None:
    with pytest.raises(BrowsePathError, match="folder"):
        await hive.browser().cat("/hive", reader())


async def test_cat_returns_a_live_wax_note_and_hides_an_expired_one(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)
    live = hive.wax_note(cell)
    expired = hive.wax_note(cell, expires_at=hive.clock.now() - timedelta(seconds=1))
    browser = hive.browser(wax=(live, expired))

    document = await browser.cat(f"/cells/{cell}/wax/{live.id}", reader())

    assert document == live
    with pytest.raises(BrowseNotFoundError):
        await browser.cat(f"/cells/{cell}/wax/{expired.id}", reader())


async def test_cat_hides_a_wax_note_under_another_cells_folder(hive: BrowseHive) -> None:
    note = hive.wax_note(new_cell_id(hive.clock))
    other = new_cell_id(hive.clock)

    with pytest.raises(BrowseNotFoundError):
        await hive.browser(wax=(note,)).cat(f"/cells/{other}/wax/{note.id}", reader())


async def test_cat_returns_a_bee_bread_entry_within_scope_and_ceiling(hive: BrowseHive) -> None:
    task = new_task_id(hive.clock)
    entry = hive.bee_bread_note(task_id=task)
    royal = hive.bee_bread_note(clearance=HoneyClearance.C2)
    browser = hive.browser(bee_bread=(entry, royal))

    document = await browser.cat(f"/bee-bread/{entry.id}", reader())

    assert document == entry
    with pytest.raises(BrowseNotFoundError):
        await browser.cat(f"/bee-bread/{royal.id}", reader(ceiling=HoneyClearance.C1))
    with pytest.raises(BrowseNotFoundError):
        await browser.cat(f"/bee-bread/{entry.id}", reader(CapabilitySet.parse("honey:read:hive")))


async def test_honey_visible_refuses_a_tainted_row(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("Tainted by a later audit.")

    assert honey_visible(row, reader())
    assert not honey_visible(row.model_copy(update={"tainted": True}), reader())


async def test_wax_visible_needs_the_cells_scope_and_the_ceiling(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)
    note: WaxNote = hive.wax_note(cell, clearance=HoneyClearance.C2)
    now = hive.clock.now()

    assert wax_visible(note, reader(), now)
    assert not wax_visible(note, reader(ceiling=HoneyClearance.C1), now)
    assert not wax_visible(note, reader(CapabilitySet.parse("honey:read:hive")), now)
    assert wax_visible(note, reader(CapabilitySet.parse(f"honey:read:{cell_scope(cell)}")), now)


async def test_bee_bread_scope_follows_the_ripening_rule(hive: BrowseHive) -> None:
    task = new_task_id(hive.clock)
    with_task: BeeBreadNote = hive.bee_bread_note(task_id=task)
    without_task = hive.bee_bread_note()

    assert bee_bread_scope(with_task) == task_scope(task)
    assert bee_bread_scope(without_task) == "hive"
    assert bee_bread_visible(without_task, reader(CapabilitySet.parse("honey:read:hive")))
