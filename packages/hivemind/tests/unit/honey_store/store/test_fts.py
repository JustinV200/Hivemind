"""Tests for hivemind.honey_store.store.fts: build_match, a safe FTS5 MATCH string builder.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/store/fts.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.store.fts for the module under test.
"""

from __future__ import annotations

import re
import sqlite3

from hypothesis import given
from hypothesis import strategies as st

from hivemind.honey_store.store.fts import MAX_MATCH_TOKENS, build_match

_TOKEN_RE = re.compile(r"\w+")


# ──────────────────────────────────────────────────────────────────────────────
# Targeted cases
# ──────────────────────────────────────────────────────────────────────────────


def test_build_match_joins_word_tokens_with_or_double_quoted() -> None:
    assert build_match("hello world") == '"hello" OR "world"'


def test_build_match_returns_none_for_no_tokens() -> None:
    assert build_match("") is None
    assert build_match("   ...!!! ---") is None


def test_build_match_truncates_to_max_match_tokens() -> None:
    text = " ".join(f"word{i}" for i in range(MAX_MATCH_TOKENS + 10))

    result = build_match(text)

    assert result is not None
    assert result.count(" OR ") == MAX_MATCH_TOKENS - 1


def test_build_match_quotes_every_token_including_the_literal_word_or() -> None:
    # A raw '"evil" OR 1=1 --' fed straight to MATCH would be an FTS5 boolean expression; here
    # every punctuation character is dropped and every surviving word (the literal text "OR"
    # included) is individually double-quoted, so it can only ever mean "search for these literal
    # words", never the FTS5 OR operator.
    result = build_match('"evil" OR 1=1 --')

    assert result == '"evil" OR "OR" OR "1" OR "1"'


# ──────────────────────────────────────────────────────────────────────────────
# Property-based: never raises, never unquoted, always FTS5-safe (ADR-0031)
# ──────────────────────────────────────────────────────────────────────────────


@given(st.text())
def test_build_match_never_raises(text: str) -> None:
    build_match(text)


@given(st.text())
def test_build_match_never_emits_an_unquoted_operator(text: str) -> None:
    result = build_match(text)
    if result is None:
        return
    # Every "OR"-separated piece must be a double-quoted, purely-word-character token; nothing
    # else (a bare NEAR, a column filter, a dangling quote) can ever appear.
    for piece in result.split(" OR "):
        assert piece.startswith('"')
        assert piece.endswith('"')
        inner = piece[1:-1]
        assert _TOKEN_RE.fullmatch(inner)


@given(st.text())
def test_build_match_result_is_accepted_by_a_real_fts5_match(text: str) -> None:
    result = build_match(text)
    if result is None:
        return
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        # Must not raise sqlite3.OperationalError ("fts5: syntax error near ...").
        connection.execute("SELECT * FROM probe WHERE probe MATCH ?", (result,)).fetchall()
    finally:
        connection.close()
