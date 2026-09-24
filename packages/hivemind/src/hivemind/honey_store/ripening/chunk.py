"""Decode a Nectar deposit's bytes into text, and cut that text into overlapping chunks (pure).

Ripening turns Nectar (raw deposits in the Honey Store, the Hive's knowledge base) into Honey:
one SUMMARY row plus one CHUNK row per slice of the text, each indexed for full-text and vector
search (ADR-0031). This module is the text half of that, with no I/O: `decode_text` reads the
bytes as UTF-8 for every textual media type (JSON pretty-printed so its structure survives as
lines) and says None for anything binary; `chunk_text` normalises the text and slides a window of
`chunk_chars` over it, ending each chunk at the last paragraph break, else the last sentence end,
else the last whitespace in the window's final 40%, else a hard cut, and starting the next one
`overlap_chars` earlier, snapped forward to a word start, so an idea split at a boundary still
reads whole in one of the two chunks. A paragraph or sentence break in the window's first quarter
is passed over, because cutting there would leave a chunk too small to carry an idea of its own
(a heading alone); the window looks for a later break instead.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.ripening`.
    Called by `hivemind.honey_store.ripening.pipeline.Ripener` for every Nectar it ripens; the
    chunks it returns become CHUNK HoneyDrafts (`hivemind.honey_store.ripening.drafts`). Calls
    into `json` and `re` only.

Key invariants:
    - Every chunk is an exact slice of `normalise_text(text)`: `chunk.text ==
      normalised[chunk.start : chunk.start + len(chunk.text)]`, and no chunk is longer than
      `chunk_chars`.
    - The chunks cover every character of the normalised text, and each starts strictly after the
      previous one, so chunking always terminates.
    - Deterministic: the same text and sizes always give the same chunks. Empty or
      whitespace-only text gives no chunks at all.
    - `decode_text` never raises: malformed JSON (or JSON too deep to re-serialise) is kept as the
      decoded text it already is, and undecodable bytes become U+FFFD.

See Also:
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the SUMMARY/CHUNK row shape.
    - hivemind.manifest.schema.honey for `chunk_chars`/`chunk_overlap_chars` and their bounds.
    - hivemind.honey_store.ripening.drafts for how chunks become HoneyDrafts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

BREAK_MIN_FILL = 0.25  # A paragraph or sentence break counts only past a quarter of the window.
WHITESPACE_MIN_FILL = 0.6  # Plain whitespace counts only inside the window's final 40%.
JSON_INDENT = 2  # Two spaces: readable, and cheap in characters for a pretty-printed payload.
# Every text/* type (plain, markdown, csv, html, xml, yaml, ...) is text by definition.
_TEXT_TOP_LEVEL = "text/"
# The application/* types that are text in all but name: NDJSON and JSON-lines, XML, YAML, TOML
# and markdown. JSON itself is listed separately because it is pretty-printed, not only decoded.
_TEXTUAL_APPLICATION_TYPES = frozenset(
    {
        "application/x-ndjson",
        "application/ndjson",
        "application/jsonl",
        "application/x-jsonlines",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/toml",
        "application/x-toml",
        "application/markdown",
        "application/x-markdown",
    }
)
_JSON_TYPE = "application/json"
_JSON_SUFFIX = "+json"  # A structured-syntax suffix (RFC 6839): application/ld+json and friends.
_TEXTUAL_SUFFIXES = ("+xml", "+yaml")  # application/atom+xml, application/xhtml+xml, ...
_NEWLINES = re.compile(r"\r\n?")  # Windows and old-Mac line endings, both become "\n".
# A newline followed by one or more lines holding nothing but spaces or tabs: one blank line.
_BLANK_RUNS = re.compile(r"\n(?:[^\S\n]*\n)+")
_PARAGRAPH_BREAK = "\n\n"
# Greedy prefixes: matching from the window's floor, `.*` backtracks from the window's end, so
# the match ends just after the LAST sentence end (or whitespace) inside the window.
_LAST_SENTENCE_END = re.compile(r".*[.!?]\s", re.DOTALL)
_LAST_WHITESPACE = re.compile(r".*\s", re.DOTALL)
_WORD_START = re.compile(r"(?<!\S)\S")  # A non-space character right after a space (or at 0).

__all__ = [
    "BREAK_MIN_FILL",
    "JSON_INDENT",
    "WHITESPACE_MIN_FILL",
    "TextChunk",
    "chunk_text",
    "decode_text",
    "normalise_text",
]


@dataclass(frozen=True, slots=True)
class TextChunk:
    """One chunk of a Nectar's normalised text, and where in that text it starts."""

    index: int  # Position among the text's chunks, from 0; becomes the CHUNK row's chunk_index.
    start: int  # Offset of the chunk's first character in `normalise_text(text)`.
    text: str  # The chunk itself: an exact slice of the normalised text.


def decode_text(content: bytes, media_type: str) -> str | None:
    """Decode a deposit's bytes as text when its media type is textual.

    Args:
        content: The deposit's raw bytes.
        media_type: Its MIME type, parameters allowed (`text/plain; charset=utf-8`); matched
            case-insensitively on the type itself.

    Returns:
        The content as UTF-8 (a byte-order mark dropped, undecodable bytes as U+FFFD), JSON
        pretty-printed; None when the media type is not textual, meaning binary.
    """
    essence = media_type.split(";", 1)[0].strip().lower()
    # JSON first: it is textual too, but its one-line wire form is worth re-indenting.
    if essence == _JSON_TYPE or essence.endswith(_JSON_SUFFIX):
        return _pretty_json(_utf8(content))
    if (
        essence.startswith(_TEXT_TOP_LEVEL)
        or essence in _TEXTUAL_APPLICATION_TYPES
        or essence.endswith(_TEXTUAL_SUFFIXES)
    ):
        return _utf8(content)
    return None


def normalise_text(text: str) -> str:
    """Unify line endings, collapse runs of blank lines to one, and trim the ends.

    Args:
        text: Any decoded text.

    Returns:
        The text `chunk_text` actually slices; empty when `text` held only whitespace.
    """
    unified = _NEWLINES.sub("\n", text)
    return _BLANK_RUNS.sub(_PARAGRAPH_BREAK, unified).strip()


def chunk_text(text: str, chunk_chars: int, overlap_chars: int) -> tuple[TextChunk, ...]:
    """Cut `text` into overlapping chunks of at most `chunk_chars`, ending at natural breaks.

    Args:
        text: Decoded text; normalised here first (`normalise_text`).
        chunk_chars: The longest chunk, in characters (`[honey.ripening] chunk_chars`). Must be
            at least 1.
        overlap_chars: How far each chunk starts before the previous one ended
            (`[honey.ripening] chunk_overlap_chars`). Must be at least 0 and below `chunk_chars`.

    Returns:
        The chunks in order; empty when the normalised text is empty.

    Raises:
        ValueError: `chunk_chars` is below 1, or `overlap_chars` is negative or not below
            `chunk_chars` (the manifest already refuses both; this guards a direct caller).
    """
    if chunk_chars < 1 or not 0 <= overlap_chars < chunk_chars:
        raise ValueError(
            f"chunk_text needs chunk_chars >= 1 and 0 <= overlap_chars < chunk_chars, got "
            f"chunk_chars={chunk_chars}, overlap_chars={overlap_chars}."
        )
    normalised = normalise_text(text)
    chunks: list[TextChunk] = []
    start = 0
    # Each pass takes one window from `start`; the loop ends once a window reaches the text's end.
    while start < len(normalised):
        end = _window_end(normalised, start, chunk_chars)
        chunks.append(TextChunk(index=len(chunks), start=start, text=normalised[start:end]))
        if end >= len(normalised):
            break
        start = _next_start(normalised, start, end, overlap_chars)
    return tuple(chunks)


def _utf8(content: bytes) -> str:
    """Decode `content` as UTF-8, dropping a byte-order mark and replacing undecodable bytes."""
    return content.decode("utf-8-sig", errors="replace")


def _pretty_json(text: str) -> str:
    """Re-indent `text` as JSON, or return it unchanged when it does not parse as JSON."""
    try:
        return json.dumps(json.loads(text), indent=JSON_INDENT, ensure_ascii=False)
    except (ValueError, RecursionError):
        # ValueError: not JSON at all (JSONDecodeError), or an integer too long to convert.
        # RecursionError: nested deeper than the decoder or the indenting encoder can walk. Either
        # way the decoded text is still worth indexing as it stands.
        return text


def _window_end(text: str, start: int, chunk_chars: int) -> int:
    """Return where the chunk starting at `start` ends: a natural break, else a hard cut."""
    limit = start + chunk_chars
    # The rest of the text fits in one window: it is the last chunk.
    if limit >= len(text):
        return len(text)
    floor = start + int(chunk_chars * BREAK_MIN_FILL)
    paragraph = text.rfind(_PARAGRAPH_BREAK, floor, limit)
    if paragraph != -1:
        return paragraph + len(_PARAGRAPH_BREAK)
    sentence = _LAST_SENTENCE_END.match(text, floor, limit)
    if sentence is not None:
        return sentence.end()
    whitespace = _LAST_WHITESPACE.match(text, start + int(chunk_chars * WHITESPACE_MIN_FILL), limit)
    if whitespace is not None:
        return whitespace.end()
    return limit  # One unbroken run (a long token, base64): a hard cut is all that is left.


def _next_start(text: str, start: int, end: int, overlap_chars: int) -> int:
    """Return where the chunk after `[start, end)` starts: `overlap_chars` back, at a word start.

    Always in `(start, end]`, so chunking advances and never leaves a gap between two chunks.
    """
    candidate = end - overlap_chars
    # A chunk no longer than the overlap: stepping back would not move past its own start.
    if candidate <= start:
        return end
    word = _WORD_START.search(text, candidate, end)
    return word.start() if word is not None else end
