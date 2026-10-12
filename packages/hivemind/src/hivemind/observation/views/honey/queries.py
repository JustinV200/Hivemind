"""Define the Honey browser's query strings and the opaque cursor its pages continue from.

The browser pages a folder, and a search pages the retriever's ranked hits, by offset. The cursor
a page carries hides that offset, so a client passes it back unread and never builds one: it is
URL-safe text, bounded, and a malformed or forged one is refused when the query is validated
(a ``422``), never guessed at. ``HoneyTreeQuery`` names a folder to list and ``HoneySearchQuery``
a folder to search and the words to find.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.views.honey``.
    Used by ``hivemind.entrance.routes.hive.honey`` for the query strings and by the Honey
    reads for the offset the cursor names. Calls into pydantic only.

Key invariants:
    - A cursor decodes to an offset of 0 to ``MAX_CURSOR_OFFSET``, or is refused.
    - ``encode_honey_cursor`` and ``decode_honey_cursor`` are exact inverses.

See Also:
    - hivemind.observation.api.thoughts for the other cursor-paged read.
"""

from __future__ import annotations

import base64
import binascii

from pydantic import BaseModel, ConfigDict, Field, field_validator

from waggle.messages.honey.exchange import MAX_QUERY_CHARS

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every query here: immutable, no strays.

DEFAULT_HONEY_PAGE = 50  # Entries a folder page answers when the caller does not say.
MAX_HONEY_PAGE = 200  # Most entries one folder page answers.
DEFAULT_SEARCH_PAGE = 10  # Hits a search page answers when the caller does not say.
MAX_SEARCH_PAGE = 50  # Most hits one search page answers: the retriever's own ceiling per query.
MAX_HONEY_PATH_CHARS = 512  # A folder and two ids with room to spare, as the browser bounds it.
MAX_CURSOR_CHARS = 32  # An opaque cursor is a few characters; anything longer is not one.
MAX_CURSOR_OFFSET = 1_000_000  # Far past any folder; keeps a forged cursor from asking for more.
ROOT_PATH = "/"  # The tree's root, and the folder a tree read without a path opens.
_CURSOR_TAG = "o"  # What a cursor's decoded text starts with, before the offset.

__all__ = [
    "DEFAULT_HONEY_PAGE",
    "DEFAULT_SEARCH_PAGE",
    "MAX_HONEY_PAGE",
    "MAX_SEARCH_PAGE",
    "HoneySearchQuery",
    "HoneyTreeQuery",
    "decode_honey_cursor",
    "encode_honey_cursor",
]


class HoneyTreeQuery(BaseModel):
    """A folder read's query string: which folder, where to continue from, how many."""

    model_config = _CONFIG

    path: str = Field(
        default=ROOT_PATH,
        max_length=MAX_HONEY_PATH_CHARS,
        description="The folder to list: / by default, /hive, /cells/<id>, /cells/<id>/wax ...",
    )
    cursor: str | None = Field(
        default=None,
        max_length=MAX_CURSOR_CHARS,
        description="A previous page's next_cursor: continue after it.",
    )
    limit: int = Field(
        default=DEFAULT_HONEY_PAGE, ge=1, le=MAX_HONEY_PAGE, description="Most entries."
    )

    @field_validator("cursor")
    @classmethod
    def _cursor_decodes(cls, value: str | None) -> str | None:
        """Refuse a cursor that does not decode, so the route answers 422 instead of guessing."""
        if value is not None:
            decode_honey_cursor(value)
        return value

    @property
    def offset(self) -> int:
        """Where the page starts: what the cursor names, or the beginning without one."""
        return decode_honey_cursor(self.cursor) if self.cursor is not None else 0


class HoneySearchQuery(BaseModel):
    """A search's query string: which folder, what to look for, where to continue, how many."""

    model_config = _CONFIG

    path: str = Field(
        default=ROOT_PATH,
        max_length=MAX_HONEY_PATH_CHARS,
        description="The folder to search: / (every scope the viewer may read), an index "
        "folder or one scope's folder.",
    )
    q: str = Field(min_length=1, max_length=MAX_QUERY_CHARS, description="The words to find.")
    cursor: str | None = Field(
        default=None,
        max_length=MAX_CURSOR_CHARS,
        description="A previous page's next_cursor: continue after it.",
    )
    limit: int = Field(
        default=DEFAULT_SEARCH_PAGE, ge=1, le=MAX_SEARCH_PAGE, description="Most hits."
    )

    @field_validator("cursor")
    @classmethod
    def _cursor_decodes(cls, value: str | None) -> str | None:
        """Refuse a cursor that does not decode, so the route answers 422 instead of guessing."""
        if value is not None:
            decode_honey_cursor(value)
        return value

    @property
    def offset(self) -> int:
        """Where the page starts: what the cursor names, or the beginning without one."""
        return decode_honey_cursor(self.cursor) if self.cursor is not None else 0


def encode_honey_cursor(offset: int) -> str:
    """Name a position in a folder or a search as an opaque cursor.

    Args:
        offset: How many entries or hits the earlier pages already showed.

    Returns:
        URL-safe text; ``decode_honey_cursor`` reads it back.
    """
    return base64.urlsafe_b64encode(f"{_CURSOR_TAG}{offset}".encode()).decode().rstrip("=")


def decode_honey_cursor(cursor: str) -> int:
    """Read a cursor back into the offset it names.

    Args:
        cursor: A string from ``encode_honey_cursor``.

    Returns:
        The offset, 0 to ``MAX_CURSOR_OFFSET``.

    Raises:
        ValueError: The cursor is not one this module made.
    """
    try:
        text = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode("ascii")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        raise ValueError("A cursor is URL-safe text from a previous page.") from None
    digits = text[len(_CURSOR_TAG) :]
    if not text.startswith(_CURSOR_TAG) or not digits.isdecimal():
        raise ValueError("A cursor is URL-safe text from a previous page.")
    offset = int(digits)
    if offset > MAX_CURSOR_OFFSET:
        raise ValueError("A cursor's position is out of range.")
    return offset
