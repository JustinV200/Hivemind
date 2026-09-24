"""Tests for hivemind.honey_store.browse.notes: a note proposed from a folder, both ways.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/notes.py (codingrules section 3). Runs over a real
    SQLite Honey Store (`harness`), reading the queued proposal and its trail event back.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.notes for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.errors import BrowseInputError, BrowsePathError
from hivemind.honey_store.browse.notes import (
    MAX_NOTE_TEXT_CHARS,
    NOTE_PROPOSED_KIND,
    CellWaxProposal,
    QueuedHoneyNote,
    propose_note,
)
from hivemind.honey_store.scope import task_scope
from waggle.ids import new_cell_id, new_task_id
from waggle.messages.cell.wax import MAX_WAX_TEXT_CHARS
from waggle.messages.honey.exchange import MAX_TITLE_CHARS


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


async def test_propose_from_hive_queues_a_note_and_records_counts_only(hive: BrowseHive) -> None:
    proposal = await propose_note(hive.deps(), "/hive", " Build tip ", "Run make clean first.")

    assert isinstance(proposal, QueuedHoneyNote)
    assert (proposal.scope, proposal.clearance) == ("hive", HoneyClearance.C2)
    (queued,) = await hive.store.pending_proposals(10)
    assert (queued.id, queued.scope, queued.title, queued.text) == (
        proposal.proposal_id,
        "hive",
        "Build tip",
        "Run make clean first.",
    )
    (event,) = await hive.events(NOTE_PROPOSED_KIND)
    assert event.actor == "human"
    assert event.subject_id == hive.identity.hive_id
    assert event.payload == {"scope": "hive", "title_chars": 9, "text_chars": 21}


async def test_propose_keeps_a_task_folders_own_scope(hive: BrowseHive) -> None:
    task = new_task_id(hive.clock)

    proposal = await propose_note(hive.deps(), f"/tasks/{task}", "Note", "About this task.")

    assert isinstance(proposal, QueuedHoneyNote)
    assert proposal.scope == task_scope(task)


@pytest.mark.parametrize("path", ["/", "/bees", "/bee-bread", "/cells"])
async def test_propose_from_a_folder_with_no_scope_joins_shared_knowledge(
    hive: BrowseHive, path: str
) -> None:
    proposal = await propose_note(hive.deps(), path, "Note", "Shared.")

    assert isinstance(proposal, QueuedHoneyNote)
    assert proposal.scope == "hive"


async def test_propose_from_a_cell_folder_describes_cell_wax_and_writes_nothing(
    hive: BrowseHive,
) -> None:
    cell = new_cell_id(hive.clock)
    paths = (f"/cells/{cell}", f"/cells/{cell}/wax", f"/cells/{cell}/honey_x")

    proposals = [await propose_note(hive.deps(), path, "Disk", "It fills up.") for path in paths]

    for proposal in proposals:
        assert isinstance(proposal, CellWaxProposal)
        assert (proposal.cell_id, proposal.text) == (cell, "It fills up.")
        assert proposal.clearance is HoneyClearance.C2
        assert "Disk" in proposal.reason
    assert await hive.store.pending_proposals(10) == ()
    assert await hive.events(NOTE_PROPOSED_KIND) == []


async def test_propose_refuses_a_cell_folder_whose_id_is_not_a_cell_id(hive: BrowseHive) -> None:
    with pytest.raises(BrowsePathError, match="not a Cell id"):
        await propose_note(hive.deps(), "/cells/notacell", "t", "x")


@pytest.mark.parametrize(
    ("title", "text"),
    [
        ("   ", "text"),
        ("x" * (MAX_TITLE_CHARS + 1), "text"),
        ("title", " \n "),
        ("title", "x" * (MAX_NOTE_TEXT_CHARS + 1)),
    ],
)
async def test_propose_refuses_an_empty_or_oversized_field(
    hive: BrowseHive, title: str, text: str
) -> None:
    with pytest.raises(BrowseInputError):
        await propose_note(hive.deps(), "/hive", title, text)
    assert await hive.store.pending_proposals(10) == ()


async def test_propose_refuses_wax_text_over_the_wire_bound(hive: BrowseHive) -> None:
    cell = new_cell_id(hive.clock)

    with pytest.raises(BrowseInputError, match="Cell Wax"):
        await propose_note(hive.deps(), f"/cells/{cell}", "t", "x" * (MAX_WAX_TEXT_CHARS + 1))
