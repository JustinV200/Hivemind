"""Test hivemind.entrance.help.retrieval.rank: BM25 puts the section about the question first.

Fits into the Hive:
    Mirrors src/hivemind/entrance/help/retrieval/rank.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest

from hivemind.entrance.help.retrieval import KeywordIndex, tokenize

DOCUMENTS = [
    "Every Cell in one table with its Warden, the Warden's mode. Click a header to sort.",
    "A Warden supervises one Cell and spawns Workers; it never provisions.",
    "Spend per goal, per slot and per provider.",
]
TITLES = ["Fleet", "Warden", "Cost"]


@pytest.mark.parametrize(
    ("query", "first"),
    [
        ("how do I sort the table", 0),  # The words shared with one document alone.
        ("what is a warden", 1),  # Named twice in passing by the Fleet's text; the title decides.
        ("cost per provider", 2),
    ],
)
def test_the_document_about_the_query_ranks_first(query: str, first: int) -> None:
    index = KeywordIndex(DOCUMENTS, TITLES)

    ranked = index.rank(query, limit=3)

    assert ranked[0][0] == first
    assert all(score > 0 for _, score in ranked)


def test_stop_words_and_absent_words_match_nothing() -> None:
    index = KeywordIndex(DOCUMENTS, TITLES)

    assert tokenize("What is the?") == []
    assert index.rank("what is the", limit=3) == []
    assert index.rank("xyzzy", limit=3) == []
