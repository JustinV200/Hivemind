"""Unit tests for hivemind.exoskeleton.browser.keys: chords in the browser's own spelling."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.browser.keys import ENTER, browser_chord


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ("Return", ENTER),
        ("Enter", ENTER),
        ("ctrl+a", "Control+a"),
        ("ctrl+shift+Tab", "Control+Shift+Tab"),
        ("super+l", "Meta+l"),
        ("BackSpace", "Backspace"),
        ("Page_Down", "PageDown"),
        ("Left", "ArrowLeft"),
        ("Escape", "Escape"),
        ("space", "Space"),
        ("f5", "F5"),
        ("F12", "F12"),
        ("A", "A"),
        ("7", "7"),
    ],
)
def test_browser_chord_translates_every_alias(keys: str, expected: str) -> None:
    assert browser_chord(keys) == expected


@pytest.mark.parametrize("keys", ["", "ctrl+", "ctrl a", "a+b+", "semi;colon"])
def test_browser_chord_refuses_what_is_not_a_chord(keys: str) -> None:
    with pytest.raises(ValueError, match="not a key chord"):
        browser_chord(keys)


@pytest.mark.parametrize("keys", ["Hyperspace", "ctrl+Warp", "F25"])
def test_browser_chord_refuses_a_key_it_does_not_know(keys: str) -> None:
    with pytest.raises(ValueError, match="not a key the browser knows"):
        browser_chord(keys)
