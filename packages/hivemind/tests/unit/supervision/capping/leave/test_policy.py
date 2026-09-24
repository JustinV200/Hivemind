"""Unit tests for hivemind.supervision.capping.leave.policy: decide, every row of the table."""

from __future__ import annotations

import pytest

from hivemind.cell import AccessLevel, CombShieldLevel
from hivemind.supervision.capping.leave.model import (
    LeaveCellFacts,
    LeaveRequest,
    LeaveVerdict,
    PathClass,
)
from hivemind.supervision.capping.leave.policy import decide
from hivemind.supervision.capping.leave.table import load_leave_policy

_TABLE = load_leave_policy()


def _request(
    path_class: PathClass, *, size: int = 100, is_executable: bool = False
) -> LeaveRequest:
    return LeaveRequest(
        path="/home/op/x", path_class=path_class, size=size, is_executable=is_executable
    )


def _cell(
    *,
    access_level: AccessLevel = AccessLevel.FULL,
    comb_shield: CombShieldLevel = CombShieldLevel.MEADOW,
    is_hive_stand: bool = True,
) -> LeaveCellFacts:
    return LeaveCellFacts(
        access_level=access_level, comb_shield=comb_shield, is_hive_stand=is_hive_stand
    )


def test_decide_undeclared_is_always_deny_before_anything_else() -> None:
    """Roadmap 5.0b's hard rule: undeclared beats every other input, even the most permissive."""
    request = _request(PathClass.KEEP_ROOT, is_executable=False)
    cell = _cell(
        access_level=AccessLevel.FULL, comb_shield=CombShieldLevel.MEADOW, is_hive_stand=True
    )

    result = decide(request, cell, False, _TABLE)

    assert result is LeaveVerdict.DENY


@pytest.mark.parametrize("access_level", [AccessLevel.READ_ONLY, AccessLevel.SCRATCH])
def test_decide_read_only_and_scratch_access_always_deny(access_level: AccessLevel) -> None:
    request = _request(PathClass.KEEP_ROOT)  # Even the most permissive class.
    cell = _cell(access_level=access_level)

    assert decide(request, cell, True, _TABLE) is LeaveVerdict.DENY


def test_decide_night_veil_always_denies() -> None:
    request = _request(PathClass.KEEP_ROOT)
    cell = _cell(comb_shield=CombShieldLevel.NIGHT_VEIL)

    assert decide(request, cell, True, _TABLE) is LeaveVerdict.DENY


def test_decide_keep_root_allows_even_when_executable() -> None:
    request = _request(PathClass.KEEP_ROOT, is_executable=True)
    cell = _cell()

    assert decide(request, cell, True, _TABLE) is LeaveVerdict.ALLOW


def test_decide_executable_outside_keep_root_always_asks() -> None:
    request = _request(PathClass.HOME, is_executable=True, size=1)
    cell = _cell()

    assert decide(request, cell, True, _TABLE) is LeaveVerdict.ASK


@pytest.mark.parametrize(
    ("path_class", "is_hive_stand", "expected"),
    [
        (PathClass.STARTUP, True, LeaveVerdict.ASK),
        (PathClass.STARTUP, False, LeaveVerdict.DENY),
        (PathClass.SYSTEM, True, LeaveVerdict.ASK),
        (PathClass.SYSTEM, False, LeaveVerdict.DENY),
        (PathClass.OTHER, True, LeaveVerdict.ASK),
        (PathClass.OTHER, False, LeaveVerdict.DENY),
    ],
)
def test_decide_startup_system_other_split_by_hive_stand(
    path_class: PathClass, is_hive_stand: bool, expected: LeaveVerdict
) -> None:
    request = _request(path_class)
    cell = _cell(is_hive_stand=is_hive_stand)

    assert decide(request, cell, True, _TABLE) is expected


def test_decide_home_under_threshold_hive_stand_allows() -> None:
    request = _request(PathClass.HOME, size=100)
    cell = _cell(is_hive_stand=True)

    assert decide(request, cell, True, _TABLE) is LeaveVerdict.ALLOW


def test_decide_home_under_threshold_borrowed_asks() -> None:
    request = _request(PathClass.HOME, size=100)
    cell = _cell(is_hive_stand=False)

    assert decide(request, cell, True, _TABLE) is LeaveVerdict.ASK


@pytest.mark.parametrize("is_hive_stand", [True, False])
def test_decide_home_over_threshold_always_asks(is_hive_stand: bool) -> None:
    over = _TABLE.general.max_home_bytes + 1
    request = _request(PathClass.HOME, size=over)
    cell = _cell(is_hive_stand=is_hive_stand)

    assert decide(request, cell, True, _TABLE) is LeaveVerdict.ASK
