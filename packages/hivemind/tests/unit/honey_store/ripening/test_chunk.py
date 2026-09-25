"""Tests for hivemind.honey_store.ripening.chunk: decoding Nectar bytes and chunking the text.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/ripening/chunk.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.ripening.chunk for the module under test.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hivemind.honey_store.ripening.chunk import (
    TextChunk,
    chunk_text,
    decode_text,
    normalise_text,
)

# Text drawn from words, sentence ends and every kind of line break, so the property tests reach
# the paragraph, sentence, whitespace and hard-cut branches of the chunker alike.
_PIECES = st.sampled_from(
    ["alpha", "beta", "gamma", "x" * 90, ". ", "! ", "? ", " ", "  ", "\n", "\n\n", "\r\n", "\t"]
)
_TEXTS = st.lists(_PIECES, max_size=120).map("".join) | st.text(max_size=600)
_SIZES = st.integers(min_value=1, max_value=120).flatmap(
    lambda chunk: st.tuples(st.just(chunk), st.integers(min_value=0, max_value=chunk - 1))
)


# ──────────────────────────────────────────────────────────────────────────────
# decode_text
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "media_type",
    [
        "text/plain",
        "text/markdown",
        "Text/CSV; charset=utf-8",
        "application/x-ndjson",
        "application/xml",
        "application/atom+xml",
        "application/yaml",
        "application/toml",
        "application/markdown",
    ],
)
def test_decode_text_reads_textual_media_types_as_utf8(media_type: str) -> None:
    assert decode_text("café {1}".encode(), media_type) == "café {1}"


@pytest.mark.parametrize(
    "media_type", ["application/octet-stream", "image/png", "application/pdf", "audio/wav"]
)
def test_decode_text_says_none_for_binary_media_types(media_type: str) -> None:
    assert decode_text(b"\x89PNG\r\n", media_type) is None


def test_decode_text_pretty_prints_json_and_structured_json_suffixes() -> None:
    raw = b'{"a": 1, "b": [true, null]}'

    for media_type in ("application/json", "application/ld+json; charset=utf-8"):
        assert decode_text(raw, media_type) == '{\n  "a": 1,\n  "b": [\n    true,\n    null\n  ]\n}'


def test_decode_text_keeps_json_that_does_not_parse_as_it_is() -> None:
    assert decode_text(b'{"a": ', "application/json") == '{"a": '


def test_decode_text_keeps_json_too_deep_to_re_indent_as_it_is() -> None:
    deep = "[" * 100_000 + "]" * 100_000

    assert decode_text(deep.encode(), "application/json") == deep


def test_decode_text_drops_a_byte_order_mark_and_replaces_invalid_bytes() -> None:
    assert decode_text(b"\xef\xbb\xbfok \xff done", "text/plain") == "ok � done"


# ──────────────────────────────────────────────────────────────────────────────
# normalise_text and chunk_text: targeted cases
# ──────────────────────────────────────────────────────────────────────────────


def test_normalise_text_unifies_newlines_and_collapses_blank_runs() -> None:
    assert normalise_text("  a\r\nb\rc\n\n \n\t\nd  ") == "a\nb\nc\n\nd"


@pytest.mark.parametrize("text", ["", "   ", "\n\n\t\r\n"])
def test_chunk_text_gives_no_chunks_for_empty_or_blank_text(text: str) -> None:
    assert chunk_text(text, 100, 10) == ()


def test_chunk_text_keeps_a_short_text_whole() -> None:
    assert chunk_text("One short finding.", 100, 10) == (TextChunk(0, 0, "One short finding."),)


def test_chunk_text_ends_a_chunk_at_the_last_paragraph_break_in_the_window() -> None:
    text = "First paragraph here.\n\nSecond one is here.\n\nThird paragraph runs on and on."

    chunks = chunk_text(text, 50, 0)

    assert chunks[0].text == "First paragraph here.\n\nSecond one is here.\n\n"
    assert chunks[1].text == "Third paragraph runs on and on."


def test_chunk_text_prefers_a_sentence_end_when_no_paragraph_break_fits() -> None:
    text = "One sentence ends here. Another starts and goes on well past the window edge."

    assert chunk_text(text, 40, 0)[0].text == "One sentence ends here. "


def test_chunk_text_falls_back_to_whitespace_in_the_windows_final_part() -> None:
    text = "words without any sentence ending at all keep flowing on"

    assert chunk_text(text, 30, 0)[0].text == "words without any sentence "


def test_chunk_text_hard_cuts_one_unbroken_run() -> None:
    chunks = chunk_text("x" * 25, 10, 0)

    assert [chunk.text for chunk in chunks] == ["x" * 10, "x" * 10, "x" * 5]


def test_chunk_text_does_not_cut_a_heading_off_on_its_own() -> None:
    text = "Title\n\n" + "A long paragraph sentence. " * 5

    first = chunk_text(text, 80, 0)[0]

    assert first.text.startswith("Title\n\nA long paragraph")


def test_chunk_text_starts_the_next_chunk_overlap_back_at_a_word_start() -> None:
    text = "alpha beta gamma delta. epsilon zeta eta theta iota kappa lambda mu."

    first, second = chunk_text(text, 40, 12)[:2]

    # 24 - 12 = 12 lands inside "gamma"; the next word start after it is "delta", which the first
    # chunk also holds: the overlap repeats whole words, never half of one.
    assert first.text == "alpha beta gamma delta. "
    assert second.start == text.index("delta")


@pytest.mark.parametrize(("chunk_chars", "overlap"), [(0, 0), (10, 10), (10, -1)])
def test_chunk_text_refuses_sizes_that_cannot_advance(chunk_chars: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="chunk_text needs"):
        chunk_text("some text", chunk_chars, overlap)


# ──────────────────────────────────────────────────────────────────────────────
# Properties (codingrules 14.3: the Ripening chunker is property-tested)
# ──────────────────────────────────────────────────────────────────────────────


@given(text=_TEXTS, sizes=_SIZES)
def test_every_chunk_is_an_exact_slice_no_longer_than_the_window(
    text: str, sizes: tuple[int, int]
) -> None:
    chunk_chars, overlap = sizes
    normalised = normalise_text(text)

    chunks = chunk_text(text, chunk_chars, overlap)

    for chunk in chunks:
        assert 0 < len(chunk.text) <= chunk_chars
        assert normalised[chunk.start : chunk.start + len(chunk.text)] == chunk.text


@given(text=_TEXTS, sizes=_SIZES)
def test_chunks_cover_every_character_and_always_advance(text: str, sizes: tuple[int, int]) -> None:
    chunk_chars, overlap = sizes
    normalised = normalise_text(text)

    chunks = chunk_text(text, chunk_chars, overlap)

    covered_to = 0
    for index, chunk in enumerate(chunks):
        assert chunk.index == index
        assert chunk.start <= covered_to  # No gap after the previous chunk.
        assert index == 0 or chunk.start > chunks[index - 1].start  # Strictly advancing.
        covered_to = max(covered_to, chunk.start + len(chunk.text))
    assert covered_to == len(normalised)
    assert (chunks == ()) == (normalised == "")


@given(text=_TEXTS, sizes=_SIZES)
def test_chunk_text_is_deterministic(text: str, sizes: tuple[int, int]) -> None:
    assert chunk_text(text, *sizes) == chunk_text(text, *sizes)


@given(text=st.text(alphabet=" \t\r\n", max_size=50), sizes=_SIZES)
def test_whitespace_only_text_never_gives_a_chunk(text: str, sizes: tuple[int, int]) -> None:
    assert chunk_text(text, *sizes) == ()
