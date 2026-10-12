"""Test hivemind.observation.views.honey.search: a folder's search as one page of ranked hits.

Over a real SQLite Honey Store and a full-text retriever: a hit names the item to open, its scope,
label and provenance, and carries a bounded snippet; a page is a slice of the ranking, with a
cursor only when a hit lies beyond it; nothing of the retriever's own withheld count is shown.

Fits into the Hive:
    Mirrors src/hivemind/observation/views/honey/search.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from unit.honey_store.browse.harness import BrowseHive, open_browse_hive, reader

from hivemind.cell import HoneyClearance
from hivemind.observation.views.honey.queries import decode_honey_cursor
from hivemind.observation.views.honey.search import HoneySearchHit, HoneySearchPage

_WORDS = "widget factory staging config"


@pytest.fixture
async def hive(tmp_path: Path) -> BrowseHive:
    """Open a fresh temp-file Honey Store for one test."""
    return await open_browse_hive(tmp_path)


async def test_a_hit_names_the_item_its_scope_label_and_provenance(hive: BrowseHive) -> None:
    (row,) = await hive.ripen(f"The {_WORDS} lives here.", clearance=HoneyClearance.C2)

    response = await hive.browser(searchable=True).search("/hive", _WORDS, reader())
    (hit,) = (HoneySearchHit.of_hit(wire) for wire in response.hits)

    assert (hit.id, hit.path, hit.scope, hit.clearance) == (
        row.id,
        row.path,
        "hive",
        HoneyClearance.C2,
    )
    assert (hit.title, hit.snippet) == (row.title, row.summary)
    assert 0.0 <= hit.score <= 1.0
    assert (hit.provenance.task_id, hit.provenance.cell_id) == (row.task_id, row.cell_id)
    assert hit.provenance.source is None


async def test_a_page_is_a_slice_of_the_ranking_with_a_cursor_only_when_more_follow(
    hive: BrowseHive,
) -> None:
    for number in range(5):
        await hive.ripen(f"Row {number}: the {_WORDS}.")
    hits = (await hive.browser(searchable=True).search("/hive", _WORDS, reader(), 4)).hits

    first = HoneySearchPage.of_hits("/hive", hits, 0, 3, "")
    second = HoneySearchPage.of_hits("/hive", hits, 3, 3, "capped")

    assert len(first.hits) == 3 and first.next_cursor is not None
    assert decode_honey_cursor(first.next_cursor) == 3
    assert [hit.path for hit in first.hits + second.hits] == [h.honey_ref for h in hits]
    assert second.next_cursor is None and second.note == "capped"
    assert set(first.model_dump()) == {"path", "hits", "next_cursor", "note"}
