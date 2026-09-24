"""Read the operator's password at the terminal: a hidden prompt, or one line of stdin per password.

Every login at the Landing Board is the device key plus the operator's password (ADR-0033), so
every CLI command that talks to it, and every offline operation on the Hive Stand, needs the
password. It is never taken from a command-line argument (the process list and the shell history
would keep it) nor from an environment variable (any child process inherits it); it is typed at a
hidden prompt, or, for a script, read from standard input with ``--password-stdin``: one line per
password, in the order the command documents (a change reads the current one first, then the new
one). It is held as a ``pydantic.SecretStr`` from the moment it is read, so no ``repr``, log line
or error can show it.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Called by every command group that
    needs the password (``hive entrance``, ``hive run --remote``, ``hive inbox --remote``). Calls
    into ``typer`` (the prompt) and the standard input only.

Key invariants:
    - A password is never echoed, logged, or accepted from argv or the environment.
    - An empty password is refused here, before any Argon2id work or network call.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Login is the
      device key plus the password".
"""

from __future__ import annotations

import sys

import typer
from pydantic import SecretStr

from hivemind.cli.landing.errors import LandingError

PROMPT = "Operator password"  # What the hidden prompt asks for.
NEW_PROMPT = "New operator password"  # The one a set, change or reset asks for.
CURRENT_PROMPT = "Current operator password"  # What a change asks for first.
_LINE_END = "\r\n"  # A line from stdin ends with one of these, which are not the password's.

__all__ = ["CURRENT_PROMPT", "NEW_PROMPT", "PROMPT", "read_new_password", "read_password"]


def read_password(from_stdin: bool, prompt: str = PROMPT) -> SecretStr:
    """Read one password: the next line of stdin, or a hidden prompt.

    Args:
        from_stdin: ``--password-stdin`` was given.
        prompt: What the hidden prompt asks for.

    Returns:
        The password, held as a secret.

    Raises:
        LandingError: stdin had no line left, or the password is empty.
    """
    text = _stdin_line(prompt) if from_stdin else typer.prompt(prompt, hide_input=True)
    return _checked(text, prompt)


def read_new_password(from_stdin: bool) -> SecretStr:
    """Read a new password: the next line of stdin, or a hidden prompt typed twice.

    Args:
        from_stdin: ``--password-stdin`` was given (a script already knows what it sends).

    Returns:
        The new password, held as a secret.

    Raises:
        LandingError: stdin had no line left, or the password is empty.
    """
    if from_stdin:
        return _checked(_stdin_line(NEW_PROMPT), NEW_PROMPT)
    # Typed twice at the terminal: a typo in a password nobody can see would lock the operator out.
    text = typer.prompt(NEW_PROMPT, hide_input=True, confirmation_prompt=True)
    return _checked(text, NEW_PROMPT)


def _stdin_line(prompt: str) -> str:
    """Read the next line of stdin without its line ending; refuse when stdin is exhausted."""
    # sys.stdin itself, not a fresh wrapper per call: a new wrapper would buffer ahead and swallow
    # the next password's line (a change reads two).
    line = sys.stdin.readline()
    if not line:
        raise LandingError(f"--password-stdin found no line for the {prompt.lower()}.")
    return line.rstrip(_LINE_END)


def _checked(text: str, prompt: str) -> SecretStr:
    """Refuse an empty password; hold any other as a secret."""
    if not text:
        raise LandingError(f"The {prompt.lower()} is empty.")
    return SecretStr(text)
