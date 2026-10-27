"""Tests for hivemind.guard.diagnostics: the read-only diagnostics allowed below FULL.

Fits into the Hive:
    Mirrors src/hivemind/guard/diagnostics.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import pytest

from hivemind.cell import AccessLevel
from hivemind.guard import DIAGNOSTIC_COMMANDS, is_diagnostic
from hivemind.guard.diagnostics import admits_diagnostic

_SHELLS = {"sh", "bash", "zsh", "dash", "cmd", "powershell", "pwsh", "env", "python", "python3"}
_METACHARACTERS = set(";|&<>$`*?(){}[]'\"\n")


def test_no_listed_diagnostic_is_a_shell_or_carries_a_shell_metacharacter() -> None:
    # The list is matched exactly, so this guards it against an entry that would run anything.
    for argv in DIAGNOSTIC_COMMANDS:
        assert argv[0] not in _SHELLS, argv
        assert not any(set(arg) & _METACHARACTERS for arg in argv), argv


@pytest.mark.parametrize(
    ("argv", "listed"),
    [
        (("ip", "route", "show"), True),
        (("ip", "route", "show", "table", "all"), False),  # Exact match only, no extra argument.
        (("cat", "/etc/shadow"), False),
        (("sh", "-c", "ip route show"), False),
    ],
)
def test_a_diagnostic_matches_only_as_listed(argv: tuple[str, ...], listed: bool) -> None:
    assert is_diagnostic(argv) is listed


@pytest.mark.parametrize(
    ("level", "role", "admitted"),
    [
        (AccessLevel.SCRATCH, "drone", True),
        (AccessLevel.FULL, "drone", True),
        (AccessLevel.READ_ONLY, "drone", False),  # A read-only device runs no command at all.
        (AccessLevel.SCRATCH, "forager", False),
        (AccessLevel.SCRATCH, "warden", False),
    ],
)
def test_only_the_drone_is_admitted_from_scratch_up(
    level: AccessLevel, role: str, admitted: bool
) -> None:
    assert admits_diagnostic(level, role) is admitted
