"""Tests for hivemind.honey_store.browse.listing: listing entries and how each item becomes one.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/listing.py (codingrules section 3). Honey rows come
    from a real store (`harness.BrowseHive`), so an entry is built from exactly what `ls` sees.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.listing for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from unit.honey_store.browse.harness import open_browse_hive

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.listing import (
    MAX_ENTRY_TITLE_CHARS,
    BrowseEntry,
    BrowseListing,
    EntryKind,
    bee_bread_entry,
    folder_entry,
    honey_entry,
    wax_entry,
)
from waggle.ids import new_cell_id, new_task_id


def test_folder_entry_carries_no_label_and_no_provenance() -> None:
    entry = folder_entry("/hive", "Shared knowledge", "3 rows")

    assert (entry.kind, entry.clearance, entry.provenance) == (EntryKind.FOLDER, None, None)
    assert (entry.path, entry.title, entry.detail) == ("/hive", "Shared knowledge", "3 rows")


async def test_honey_entry_shows_part_label_and_provenance(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    summary, chunk = await hive.ripen(
        "The deploy key rotates on Mondays.", clearance=HoneyClearance.C2, chunks=("Mondays.",)
    )

    summary_entry = honey_entry(summary)
    chunk_entry = honey_entry(chunk)

    assert (summary_entry.path, summary_entry.detail) == (summary.path, "SUMMARY")
    assert chunk_entry.detail == "CHUNK 1"
    assert summary_entry.clearance is HoneyClearance.C2
    assert summary_entry.provenance is not None
    assert summary_entry.provenance.task_id == summary.task_id
    assert summary_entry.provenance.observed_at == summary.observed_at


async def test_wax_entry_uses_the_note_text_and_severity(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    cell = new_cell_id(hive.clock)
    note = hive.wax_note(cell)

    entry = wax_entry(note)

    assert entry.kind is EntryKind.WAX
    assert entry.path == f"/cells/{cell}/wax/{note.id}"
    assert (entry.title, entry.detail) == (note.text, "CAUTION")
    assert entry.provenance is not None and entry.provenance.cell_id == cell


async def test_bee_bread_entry_previews_its_payload_first_line(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    task = new_task_id(hive.clock)
    note = hive.bee_bread_note(task_id=task, payload="make build\nlinker ok\n")

    entry = bee_bread_entry(note)

    assert (entry.kind, entry.title, entry.detail) == (
        EntryKind.BEE_BREAD,
        "make build",
        "TRANSCRIPT",
    )
    assert entry.path == f"/bee-bread/{note.id}"
    assert entry.provenance is not None and entry.provenance.task_id == task


def test_folder_entry_cuts_a_long_title_to_one_line() -> None:
    entry = folder_entry("/hive", "x" * (MAX_ENTRY_TITLE_CHARS + 50) + "\nsecond line")

    assert len(entry.title) == MAX_ENTRY_TITLE_CHARS
    assert entry.title.endswith("…")


def test_browse_listing_round_trips_through_json() -> None:
    listing = BrowseListing(
        path="/", entries=(folder_entry("/hive", "Shared"),), is_truncated=True, note="more"
    )

    assert BrowseListing.model_validate_json(listing.model_dump_json()) == listing


def test_browse_entry_refuses_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        BrowseEntry.model_validate(
            {
                "path": "/hive",
                "kind": "FOLDER",
                "title": "t",
                "detail": "",
                "clearance": None,
                "provenance": None,
                "extra": 1,
            }
        )
