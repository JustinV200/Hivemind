"""Translate a GUI step's key chord into the key names a browser presses.

A GUI step names a key chord once, in waggle's KEYS_PATTERN syntax ("ctrl+a", "Return",
"shift+Tab"), and the same step may reach a desktop (`xdotool`, which says "Return" and "ctrl")
or the browser fast path (Playwright, which says "Enter" and "Control"). This module maps the
first spelling onto the second: modifiers and the common named keys under every alias a bee is
likely to write
(xdotool's, the browser's own, the everyday ones), function keys, and any single character as it
is. Anything else is refused with a ValueError before a key is pressed, so a typo is a failed step
the bee can correct, never an unknown key pressed into a page. Both browsers use the same table,
so the fake gives a chord exactly the meaning the real browser does.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Called by `browser.playwright` and `browser.fake` for `Browser.press`. Calls into the standard
    library and waggle's KEYS_PATTERN only.

Key invariants:
    - Every chord `browser_chord` returns names only keys Playwright's keyboard knows.
    - A chord that fails KEYS_PATTERN is refused before any name is looked up.

See Also:
    - waggle.messages.capping.gui for KEYS_PATTERN.
    - hivemind.exoskeleton.browser.base for Browser.press.
"""

from __future__ import annotations

import re

from waggle.messages.capping.gui import KEYS_PATTERN

ENTER = "Enter"  # The key that submits a form: the one chord the fake browser gives a meaning to.
_CHORD = re.compile(KEYS_PATTERN)
_FUNCTION_KEY = re.compile(r"^[fF]([1-9]|1[0-9]|2[0-4])$")  # F1 to F24, as both tools number them.
# Every alias of a modifier or named key, lowercased, to the name the browser's keyboard uses.
# xdotool's spellings come first in each group because the desktop steps already use them.
_NAMED_KEYS: dict[str, str] = {
    # Modifiers.
    "ctrl": "Control",
    "control": "Control",
    "shift": "Shift",
    "alt": "Alt",
    "option": "Alt",
    "super": "Meta",
    "meta": "Meta",
    "cmd": "Meta",
    "command": "Meta",
    "win": "Meta",
    # Editing and confirming.
    "return": ENTER,
    "enter": ENTER,
    "kp_enter": ENTER,
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "backspace": "Backspace",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "space": "Space",
    # Moving around.
    "home": "Home",
    "end": "End",
    "page_up": "PageUp",
    "pageup": "PageUp",
    "prior": "PageUp",
    "page_down": "PageDown",
    "pagedown": "PageDown",
    "next": "PageDown",
    "left": "ArrowLeft",
    "arrowleft": "ArrowLeft",
    "right": "ArrowRight",
    "arrowright": "ArrowRight",
    "up": "ArrowUp",
    "arrowup": "ArrowUp",
    "down": "ArrowDown",
    "arrowdown": "ArrowDown",
    "caps_lock": "CapsLock",
    "capslock": "CapsLock",
}

__all__ = ["ENTER", "browser_chord"]


def browser_chord(keys: str) -> str:
    """Return `keys` in the browser's own spelling: "ctrl+a" becomes "Control+a".

    Args:
        keys: One chord in waggle's KEYS_PATTERN syntax: key names joined by "+".

    Returns:
        The same chord with every name translated, joined by "+" as Playwright expects.

    Raises:
        ValueError: `keys` is not a chord, or names a key this table does not know.

    Example:
        >>> browser_chord("ctrl+shift+Return")
        'Control+Shift+Enter'
    """
    if not _CHORD.fullmatch(keys):
        raise ValueError(f"{keys!r} is not a key chord (names joined by '+').")
    return "+".join(_key_name(name) for name in keys.split("+"))


def _key_name(name: str) -> str:
    """Translate one key name, or refuse it."""
    named = _NAMED_KEYS.get(name.lower())
    if named is not None:
        return named
    if _FUNCTION_KEY.match(name):
        return name.upper()
    # A single letter, digit or underscore types itself; case is kept, since "A" is not "a".
    if len(name) == 1:
        return name
    raise ValueError(f"{name!r} is not a key the browser knows.")
