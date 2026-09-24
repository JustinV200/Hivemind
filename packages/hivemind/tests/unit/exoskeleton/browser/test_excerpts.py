"""Unit tests for hivemind.exoskeleton.browser.excerpts: bounded reads and one-line details."""

from __future__ import annotations

from hivemind.exoskeleton.browser.excerpts import (
    MAX_DETAIL_CHARS,
    MAX_ELEMENT_TEXT_CHARS,
    URL_MASK,
    bounded,
    detail,
)
from waggle.messages.capping.verdict import MAX_OBSERVED_CHARS


def test_bounded_returns_text_that_fits_unchanged() -> None:
    assert bounded("short", 10) == "short"
    assert bounded("x" * 10, 10) == "x" * 10


def test_bounded_cuts_long_text_to_the_limit_and_says_how_long_it_was() -> None:
    text = "a" * 1_000

    cut = bounded(text, 100)

    assert len(cut) == 100
    assert cut.startswith("a" * 50)
    assert "1000 characters in all" in cut


def test_bounded_with_a_limit_too_small_for_the_note_is_a_plain_cut() -> None:
    assert bounded("abcdefgh", 3) == "abc"


def test_element_text_cap_is_the_verdicts_observed_cap() -> None:
    assert MAX_ELEMENT_TEXT_CHARS == MAX_OBSERVED_CHARS


def test_detail_masks_every_url_and_keeps_one_line() -> None:
    text = "net::ERR_FILE_NOT_FOUND at file:///home/x/a.html\nCall log:\n  - https://t.test/?k=1"

    line = detail(text)

    assert "file://" not in line
    assert "https://" not in line
    assert line.count(URL_MASK) == 2
    assert "\n" not in line
    assert line.startswith("net::ERR_FILE_NOT_FOUND at <url>")


def test_detail_replaces_control_characters_and_collapses_spaces() -> None:
    assert detail("a\x00\x07  b\t\tc") == "a b c"


def test_detail_is_bounded_from_the_start_or_from_the_end() -> None:
    text = "start " + "x" * 500 + " end"

    assert len(detail(text)) == MAX_DETAIL_CHARS
    assert detail(text).startswith("start")
    assert detail(text, from_end=True).endswith("end")
