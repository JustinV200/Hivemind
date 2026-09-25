"""Tests for hivemind.honey_store.browse.browser: HoneyBrowser's search, and the operator's reader.

`ls`, `cat` and `propose_note` are covered path by path in test_folders.py, test_documents.py
and test_notes.py; this module covers what the browser itself adds: a search limited to one
folder's scopes, a browser without a retriever, and `operator_reader`.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/browser.py (codingrules section 3). Runs over a real
    SQLite Honey Store with a full-text-only retriever (`harness`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.browser for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive, reader

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.browser import EMPTY_INDEX_REASON, operator_reader
from hivemind.honey_store.browse.documents import HoneyDocument
from hivemind.honey_store.browse.errors import BrowseError, BrowsePathError
from hivemind.honey_store.browse.notes import QueuedHoneyNote
from hivemind.honey_store.scope import is_readable, task_scope
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_task_id

_WORDS = "widget factory staging config"


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


async def _two_scopes(hive: BrowseHive) -> tuple[str, str]:
    """Ripen the same words into `hive` and one task scope; return both rows' paths."""
    (shared,) = await hive.ripen(f"The {_WORDS} lives in /etc/widgets.")
    (working,) = await hive.ripen(
        f"While testing, the {_WORDS} was copied.", scope=task_scope(new_task_id(hive.clock))
    )
    return shared.path, working.path


async def test_search_in_a_scope_folder_finds_only_that_scope(hive: BrowseHive) -> None:
    shared, working = await _two_scopes(hive)
    browser = hive.browser(searchable=True)

    in_hive = await browser.search("/hive", _WORDS, reader())
    everywhere = await browser.search("/", _WORDS, reader())
    in_tasks = await browser.search("/tasks", _WORDS, reader())

    assert [hit.honey_ref for hit in in_hive.hits] == [shared]
    assert {hit.honey_ref for hit in everywhere.hits} == {shared, working}
    assert [hit.honey_ref for hit in in_tasks.hits] == [working]


async def test_search_in_an_empty_index_folder_finds_nothing(hive: BrowseHive) -> None:
    await _two_scopes(hive)

    response = await hive.browser(searchable=True).search("/cells", _WORDS, reader())

    assert response.hits == ()
    assert response.reason == EMPTY_INDEX_REASON


async def test_search_refuses_a_folder_that_holds_no_honey(hive: BrowseHive) -> None:
    with pytest.raises(BrowsePathError):
        await hive.browser(searchable=True).search("/bee-bread", _WORDS, reader())


async def test_search_needs_a_retriever(hive: BrowseHive) -> None:
    with pytest.raises(BrowseError, match="retriever"):
        await hive.browser().search("/hive", _WORDS, reader())


async def test_browser_lists_reads_and_proposes_by_path(hive: BrowseHive) -> None:
    (row,) = await hive.ripen("One shared finding.")
    browser = hive.browser()

    listing = await browser.ls("/hive", reader())
    document = await browser.cat(row.path, reader())
    proposal = await browser.propose_note("/hive", "Title", "Text.")

    assert [entry.path for entry in listing.entries] == [row.path]
    assert document == HoneyDocument(**row.model_dump(), sources=())
    assert isinstance(proposal, QueuedHoneyNote)


def test_operator_reader_reads_every_scope_up_to_its_ceiling() -> None:
    hive_id = new_hive_id(FakeClock())

    operator = operator_reader(hive_id, HoneyClearance.C1)

    assert (operator.requester, operator.ceiling, operator.is_night_veil) == (
        hive_id,
        HoneyClearance.C1,
        False,
    )
    assert is_readable("hive", operator.capabilities)
    assert is_readable("cell:cell_01ARZ3NDEKTSV4RRFFQ69G5FAV", operator.capabilities)
