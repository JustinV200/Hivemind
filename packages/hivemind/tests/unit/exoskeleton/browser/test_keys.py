"""Unit tests for hivemind.exoskeleton.browser.keys: chords in the browser's own spelling."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.browser.keys import ENTER, browser_chord, chrome_chord


@pytest.mark.parametrize(
    ("keys", "window"),
    [
        ("F12", "DevTools"),
        ("shift+f12", "DevTools"),
        ("ctrl+shift+i", "DevTools"),
        ("ctrl+shift+J", "DevTools"),
        ("control+shift+c", "DevTools"),
        ("cmd+alt+i", "DevTools"),
        ("ctrl+s", "Save As"),
        ("ctrl+shift+s", "Save As"),
        ("cmd+s", "Save As"),
        ("ctrl+o", "Open File"),
        ("ctrl+p", "Print"),
        ("ctrl+shift+p", "Print"),
    ],
)
def test_chrome_chord_names_the_browser_window_a_chord_opens(keys: str, window: str) -> None:
    assert chrome_chord(keys) == window


@pytest.mark.parametrize(
    "keys", ["ctrl+c", "ctrl+a", "ctrl+i", "shift+i", "s", "Return", "alt+p", "F5", "not a chord"]
)
def test_chrome_chord_lets_every_other_chord_through(keys: str) -> None:
    assert chrome_chord(keys) is None


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
