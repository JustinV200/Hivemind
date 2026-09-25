"""Build a safe FTS5 MATCH string from arbitrary query text; never raises, never injects.

SQLite's FTS5 MATCH syntax has its own operators (`AND`, `OR`, `NOT`, `NEAR`, `"phrase"`,
`column:`, `-prefix`) that a raw query string could accidentally, or maliciously, trigger. Honey
is retrieved for a model's own use (codingrules section 15: "Nothing is executed from the Honey
Store"), so `build_match` treats every query the same way: pull out its word tokens, drop
everything else, and double-quote each one, so the built string can only ever express "any of
these literal words", never a raw FTS5 operator. Two refinements keep the words that remain worth
matching: a short list of English function words ("the", "is", "how") is dropped whenever the
query has any other word (FTS5 indexes them, so a query of "how do I restart the widget" would
otherwise match nearly every row on "the"), and a word repeated in the query is sent once, so a
repeated word cannot spend the token allowance twice.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the honey_store package. Called
    by whatever builds a text query (`hivemind.honey_store.honey`, a later dispatch, and
    `hivemind.honey_store.store.sqlite.search`, this dispatch's own `search_text`/`count_withheld`
    callers) before it reaches `HoneyStore.search_text`/`.count_withheld`. Calls into `re` only.

Key invariants:
    - `build_match` never raises: any `str` input produces either a MATCH string or None.
    - Every character `build_match` emits outside a token is one of `"`, ` `, `O`, `R` (the literal
      text `" OR "` between quoted tokens); no FTS5 operator ever appears unquoted.
    - A query made only of function words still matches them (nothing else is left to search
      for); otherwise none of them is ever sent.
    - Every string `build_match` returns is accepted by a real FTS5 `MATCH` (property-tested,
      `tests/unit/honey_store/test_fts.py`, against an in-memory `honey_fts`-shaped table).

See Also:
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for "a query string never reaches MATCH
      ... reduced to at most 32 word tokens, each quoted, joined with OR".
    - .claude/codingrules.md section 15 for "Nothing is executed from the Honey Store."
    - hivemind.honey_store.store.protocol for `search_text`/`count_withheld`, this function's
      callers' own callers.
"""

from __future__ import annotations

import re

MAX_MATCH_TOKENS = 32  # ADR-0035: "at most 32 word tokens" -- long enough for a real question.
# `\w` already covers [A-Za-z0-9_] plus every Unicode letter/digit in Python's `re` (str patterns
# are Unicode by default), matching ADR-0035's "[A-Za-z0-9_]+ (unicode letters too)" exactly; no
# character in this class can ever contain a `"`, so quoting a token needs no escaping.
_TOKEN_PATTERN = re.compile(r"\w+")
# English function words: in nearly every row, so matching one says nothing about relevance, and
# on a young store (too few rows for bm25's IDF to discount them) they would rank as well as the
# words that matter. Lower-case; compared case-insensitively.
_FUNCTION_WORDS = frozenset(
    {
        "a",
        "about",
        "all",
        "also",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "me",
        "my",
        "no",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "so",
        "some",
        "such",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "to",
        "too",
        "up",
        "us",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)

__all__ = ["MAX_MATCH_TOKENS", "build_match"]


def build_match(text: str) -> str | None:
    """Reduce `text` to a safe FTS5 MATCH string: quoted word tokens joined by `OR`.

    Args:
        text: Arbitrary query text; a question, a phrase, or anything a caller typed.

    Returns:
        `'"tok1" OR "tok2" OR ...'` for at most `MAX_MATCH_TOKENS` word tokens found in `text`, in
        the order they appear; None when `text` has no token at all (an empty or punctuation-only
        query matches nothing worth searching for).
    """
    tokens = _distinct(_TOKEN_PATTERN.findall(text))
    if not tokens:
        return None  # No token survived: a caller should skip the text side of the search.
    # Function words only when nothing else is left; any content word makes them noise.
    content = [token for token in tokens if token.lower() not in _FUNCTION_WORDS]
    chosen = (content or tokens)[:MAX_MATCH_TOKENS]
    return " OR ".join(f'"{token}"' for token in chosen)


def _distinct(tokens: list[str]) -> list[str]:
    """Keep each token's first occurrence, compared case-insensitively (FTS5 folds case too)."""
    seen: set[str] = set()
    distinct: list[str] = []
    for token in tokens:
        folded = token.lower()
        if folded not in seen:
            seen.add(folded)
            distinct.append(token)
    return distinct
