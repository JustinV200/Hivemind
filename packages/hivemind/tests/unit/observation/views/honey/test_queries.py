"""Test hivemind.observation.views.honey.queries: the opaque cursor and the two query strings.

A cursor hides the offset a page continues from; it round-trips, and anything this module did not
make is refused when the query is validated, so the route answers 422 instead of guessing. The
folder and search queries bound their page sizes, their path and their words.

Fits into the Hive:
    Mirrors src/hivemind/observation/views/honey/queries.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from hivemind.observation.views.honey.queries import (
    DEFAULT_HONEY_PAGE,
    DEFAULT_SEARCH_PAGE,
    MAX_CURSOR_OFFSET,
    MAX_HONEY_PAGE,
    MAX_SEARCH_PAGE,
    HoneySearchQuery,
    HoneyTreeQuery,
    decode_honey_cursor,
    encode_honey_cursor,
)


def _forged(text: str) -> str:
    """Return what a client could forge: the base64 of `text`, unpadded, as a cursor looks."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.mark.parametrize("offset", [0, 1, 50, 199, MAX_CURSOR_OFFSET])
def test_a_cursor_round_trips_its_offset(offset: int) -> None:
    cursor = encode_honey_cursor(offset)

    assert decode_honey_cursor(cursor) == offset
    # URL-safe: nothing a query string would need to escape.
    assert all(char.isalnum() or char in "-_" for char in cursor)


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "!!",
        "%%%",
        "\u00e9",
        _forged("5"),
        _forged("o"),
        _forged("o-1"),
        _forged("oabc"),
        _forged("o1.5"),
        _forged(f"o{MAX_CURSOR_OFFSET + 1}"),
    ],
)
def test_a_cursor_this_module_did_not_make_is_refused(cursor: str) -> None:
    with pytest.raises(ValueError):
        decode_honey_cursor(cursor)


def test_the_tree_query_defaults_to_the_root_and_the_first_page() -> None:
    query = HoneyTreeQuery()

    assert (query.path, query.cursor, query.limit, query.offset) == (
        "/",
        None,
        DEFAULT_HONEY_PAGE,
        0,
    )
    assert HoneyTreeQuery(cursor=encode_honey_cursor(40)).offset == 40


@pytest.mark.parametrize(
    "fields",
    [
        {"limit": 0},
        {"limit": MAX_HONEY_PAGE + 1},
        {"cursor": "not a cursor"},
        {"path": "/" + "x" * 600},
        {"unknown": 1},
    ],
)
def test_the_tree_query_refuses_what_it_cannot_serve(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HoneyTreeQuery.model_validate(fields)


def test_the_search_query_needs_words_and_bounds_its_page() -> None:
    query = HoneySearchQuery(q="staging config", cursor=encode_honey_cursor(10))

    assert (query.path, query.limit, query.offset) == ("/", DEFAULT_SEARCH_PAGE, 10)
    assert HoneySearchQuery(q="x", limit=MAX_SEARCH_PAGE).limit == MAX_SEARCH_PAGE
    for fields in (
        {},
        {"q": ""},
        {"q": "x", "limit": MAX_SEARCH_PAGE + 1},
        {"q": "x", "cursor": "?"},
    ):
        with pytest.raises(ValidationError):
            HoneySearchQuery.model_validate(fields)
