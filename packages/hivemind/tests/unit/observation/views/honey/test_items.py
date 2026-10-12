"""Test hivemind.observation.views.honey.items: an item as a listing and as a document shows it.

Over a real SQLite Honey Store (the browse harness) and the shipped fake wax and Bee Bread
sources: a Honey row is Honey, or a note when its Nectar came from the human; wax carries its
severity, proposer and expiry; a Bee Bread entry its kind and its task's scope. The document form
adds the text, a wax note's reason and a row's extra sources, and never a folder.

Fits into the Hive:
    Mirrors src/hivemind/observation/views/honey/items.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
from builders.honey import make_honey_draft, make_nectar_draft
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive, reader

from hivemind.cell import HoneyClearance
from hivemind.common.errors import InvariantViolationError
from hivemind.honey_store import honey_event
from hivemind.honey_store.browse import BrowseEntry, EntryKind, HoneyBrowser
from hivemind.honey_store.models import NectarOrigin
from hivemind.honey_store.scope import cell_scope, folder_for_scope, task_scope
from hivemind.observation.views.honey.items import (
    HoneyItemKind,
    HoneyItemSummary,
    HoneyItemView,
    last_segment,
    provenance_view,
)
from waggle.ids import new_cell_id, new_task_id
from waggle.messages.cell.wax import WaxSeverity


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


async def _first_item(browser: HoneyBrowser, path: str) -> BrowseEntry:
    """List `path` as the operator and return its first item entry (never a folder)."""
    listing = await browser.ls(path, reader())
    return next(entry for entry in listing.entries if entry.kind is not EntryKind.FOLDER)


async def test_a_listed_honey_row_is_honey_and_the_humans_own_note_is_a_note(
    hive: BrowseHive,
) -> None:
    (honey,) = await hive.ripen("A finding.", clearance=HoneyClearance.C1)
    (note,) = await hive.ripen(
        "My own note.", scope=cell_scope(new_cell_id(hive.clock)), origin=NectarOrigin.HUMAN
    )

    listed = HoneyItemSummary.of_entry(await _first_item(hive.browser(), "/hive"))
    noted = HoneyItemSummary.of_entry(
        await _first_item(hive.browser(), folder_for_scope(note.scope))
    )

    assert (listed.kind, listed.id, listed.path) == (HoneyItemKind.HONEY, honey.id, honey.path)
    assert (listed.scope, listed.clearance, listed.size_chars) == (
        "hive",
        HoneyClearance.C1,
        len(honey.body),
    )
    assert (listed.provenance.source, listed.provenance.cell_id) == ("BEE", honey.cell_id)
    assert (listed.severity, listed.proposer, listed.expires_at) == (None, None, None)
    assert (noted.kind, noted.provenance.source) == (HoneyItemKind.NOTE, "HUMAN")


async def test_a_listed_wax_note_carries_severity_proposer_and_expiry(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)
    expiry = hive.clock.now() + timedelta(days=2)
    note = hive.wax_note(cell, severity=WaxSeverity.BLOCK, expires_at=expiry)

    listed = HoneyItemSummary.of_entry(
        await _first_item(hive.browser(wax=[note]), f"/cells/{cell}/wax")
    )

    assert (listed.kind, listed.id, listed.severity) == (
        HoneyItemKind.WAX,
        note.id,
        WaxSeverity.BLOCK,
    )
    assert (listed.proposer, listed.expires_at, listed.scope) == (
        note.proposer,
        expiry,
        cell_scope(cell),
    )
    assert listed.provenance.source == "BEE"


async def test_a_listed_bee_bread_entry_is_filed_under_its_tasks_scope(hive: BrowseHive) -> None:
    task = new_task_id(hive.clock)
    entry = hive.bee_bread_note(task_id=task, payload="make build\nok\n")

    listed = HoneyItemSummary.of_entry(
        await _first_item(hive.browser(bee_bread=[entry]), "/bee-bread")
    )

    assert (listed.kind, listed.id, listed.scope) == (
        HoneyItemKind.BEE_BREAD,
        entry.id,
        task_scope(task),
    )
    assert (listed.title, listed.detail, listed.provenance.source) == (
        "make build",
        "TRANSCRIPT",
        "BEE_BREAD",
    )
    assert listed.provenance.task_id == task


async def test_a_document_carries_its_text_reason_and_extra_sources(hive: BrowseHive) -> None:
    draft = make_nectar_draft(clock=hive.clock, content=b"shared content")
    digest = hashlib.sha256(draft.content).hexdigest()
    added = await hive.store.add_nectar(draft, digest, lambda _added: ())
    event = honey_event(hive.identity, hive.clock, "honey.ripened", added.nectar.id)
    (row,) = await hive.store.ripen(added.nectar.id, (make_honey_draft(),), event)
    other_task = new_task_id(hive.clock)
    await hive.store.add_nectar(
        draft.model_copy(update={"task_id": other_task, "source_key": "dup"}),
        digest,
        lambda _added: (),
    )
    cell = new_cell_id(hive.clock)
    wax = hive.wax_note(cell)
    bread = hive.bee_bread_note(payload="line one\nline two")
    browser = hive.browser(wax=[wax], bee_bread=[bread])

    honey = HoneyItemView.of_document(await browser.find(row.id, reader()))
    note = HoneyItemView.of_document(await browser.find(wax.id, reader()))
    entry = HoneyItemView.of_document(await browser.find(bread.id, reader()))

    assert (honey.content, honey.summary, honey.title) == (row.body, row.summary, row.title)
    assert [source.task_id for source in honey.sources] == [other_task]
    assert (note.content, note.reason, note.kind) == (wax.text, wax.reason, HoneyItemKind.WAX)
    assert (note.path, note.scope) == (f"/cells/{cell}/wax/{wax.id}", cell_scope(cell))
    assert (entry.content, entry.title, entry.scope) == ("line one\nline two", "line one", "hive")
    assert entry.size_chars == len("line one\nline two") and entry.sources == []


async def test_a_document_view_is_the_listing_view_plus_the_text(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("One line of text.")
    browser = hive.browser()

    listed = HoneyItemSummary.of_entry(await _first_item(hive.browser(), "/hive"))
    opened = HoneyItemView.of_document(await browser.find(row.id, reader()))

    assert (
        opened.model_dump(exclude={"summary", "content", "reason", "sources"})
        == listed.model_dump()
    )


def test_a_listed_item_without_provenance_is_a_bug_not_a_blank() -> None:
    with pytest.raises(InvariantViolationError):
        provenance_view(None, None)
    assert last_segment("/cells/cell_x/wax/wax_y") == "wax_y"
