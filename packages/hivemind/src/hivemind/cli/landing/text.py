"""Print what someone else wrote, and what went wrong, safely on a terminal: one line each.

Two kinds of text reach the operator's terminal from outside the CLI's own code. Strings another
party wrote: what a device said about itself when it redeemed an invite (from a device nobody
trusts yet), a question or reply the Queen relays (which can quote a model's words). On a
terminal, a control character, an escape sequence or a bidirectional override inside such a
string could rewrite what the operator sees at the moment it matters, an approval or an answer
(ADR-0033: "escapes every string the device supplied"), so ``shown`` escapes everything Python
does not call printable and bounds the length. And failures: ``describe`` turns what a command
caught into the one line it prints, naming a refused value by its field and never repeating the
value itself (it may be a password a model validated, or a device's words).

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Used by ``hivemind.cli.entrance``
    and ``hivemind.cli.remote`` wherever they print. Calls into pydantic only.

Key invariants:
    - ``shown`` never returns a character a terminal would act on.
    - ``describe`` never returns a refused value, only where it was.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md.
"""

from __future__ import annotations

from pydantic import ValidationError

MAX_SHOWN_CHARS = 120  # One foreign string on one terminal line; the rest is marked cut.
_CUT = "..."  # Marks a string cut at its limit.

__all__ = ["MAX_SHOWN_CHARS", "describe", "shown"]


def shown(text: str | None, limit: int = MAX_SHOWN_CHARS) -> str:
    """Escape a string someone else wrote so a terminal prints it as text, never as control.

    Args:
        text: The foreign string, or None.
        limit: The most characters to print before cutting.

    Returns:
        The string with every non-printable character (controls, escape sequences, bidirectional
        and other format characters, line breaks) as its Python escape, cut to ``limit``;
        ``-`` for None or empty.
    """
    if not text:
        return "-"
    escaped = "".join(
        character if character.isprintable() else _escape(character) for character in text
    )
    return escaped if len(escaped) <= limit else escaped[: limit - len(_CUT)] + _CUT


def describe(exc: BaseException) -> str:
    """Say what went wrong in one line; a refused value is named by its field, never shown.

    Args:
        exc: What a command caught.

    Returns:
        The line.
    """
    if isinstance(exc, ValidationError):
        fields = ", ".join(".".join(str(part) for part in error["loc"]) for error in exc.errors())
        return f"these values are not valid: {fields or 'the input'}."
    return str(exc) or type(exc).__name__


def _escape(character: str) -> str:
    """The escape of one non-printable character, as Python writes it in a string literal."""
    code = ord(character)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"
