"""Tests for hivemind.guard.diagnostics: the read-only diagnostics allowed below FULL.

Fits into the Hive:
    Mirrors src/hivemind/guard/diagnostics.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from builders.entrance import make_device

from hivemind.brood_chamber.task.goal_set import canonical_goal_set
from hivemind.cell import AccessLevel
from hivemind.entrance.enrol import DeviceStatus, approval_grant, device_ceiling, steward_grant
from hivemind.entrance.errors import StewardGrantError
from hivemind.guard import (
    DIAGNOSTIC_COMMANDS,
    CapabilitySet,
    InvalidCapabilityError,
    is_diagnostic,
    load_guard_policy,
    parse_submitted,
)
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


_POLICY = load_guard_policy()
# A steward that holds every command, so the reserved scope is the only reason left to refuse.
_STEWARD = make_device(
    status=DeviceStatus.APPROVED, capabilities=("entrance:steward", "exec:*", "observe")
)


def _steward_grants(reserved: str) -> object:
    """A steward's remote approval, the way the Entrance's route parses and checks it."""
    return steward_grant(_STEWARD, CapabilitySet.parse(reserved), device_ceiling(_POLICY))


_ENTRY_POINTS: dict[str, Callable[[str], object]] = {
    "submitted set": lambda reserved: parse_submitted("observe", reserved),
    "operator approval": lambda reserved: approval_grant(_POLICY, ("observe", reserved)),
    "steward grant": _steward_grants,
    "goal capability set": lambda reserved: canonical_goal_set(("fs:read:**", reserved)),
}


@pytest.mark.parametrize("entry", sorted(_ENTRY_POINTS))
@pytest.mark.parametrize("reserved", ["exec:@diagnostic", "exec:@anything"])
def test_no_entry_from_outside_the_hive_accepts_the_reserved_exec_scope(
    entry: str, reserved: str
) -> None:
    # Only a cut-off Warden's own diagnostic task may name it; a submitter that could would turn
    # any Drone of its goal into a "diagnostic Drone" below FULL.
    with pytest.raises((InvalidCapabilityError, StewardGrantError, ValueError), match="reserved"):
        _ENTRY_POINTS[entry](reserved)
