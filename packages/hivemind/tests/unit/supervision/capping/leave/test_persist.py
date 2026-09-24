"""Unit tests for hivemind.supervision.capping.leave.persist: decide_persist, build_leave_context.

Uses `tmp_path` for every "resolved"/"home" argument (never a hand-written path literal): `decide_
persist`/`build_leave_context` convert a real, host-native `Path` to text via `str()`, so the
`os_family` they are given must match whatever produced that text -- `tmp_path` and `os.name`
staying paired is what keeps this test portable across a Windows or POSIX CI host, the same way
`hivemind.supervision.capping.leave.classify`'s own tests (given plain strings, never a `Path`)
exercise the other OS family without needing a real machine of that kind.
"""

from __future__ import annotations

import os
from pathlib import Path

from builders.cells import make_capabilities, make_cell

from hivemind.cell import (
    HIVE_STAND_SOURCE,
    AccessLevel,
    Cell,
    CellKind,
    CombShieldLevel,
    OsFamily,
)
from hivemind.cell.leavings import ApprovedBy
from hivemind.supervision.capping.leave.model import LeaveVerdict
from hivemind.supervision.capping.leave.persist import build_leave_context, decide_persist
from hivemind.supervision.capping.leave.table import load_leave_policy
from waggle.messages import PlannedLeaving

_HOST_OS_FAMILY = OsFamily.WINDOWS if os.name == "nt" else OsFamily.LINUX


def _cell(
    *,
    kind: CellKind = CellKind.REAL,
    source: str = HIVE_STAND_SOURCE,
    access_level: AccessLevel = AccessLevel.FULL,
    comb_shield: CombShieldLevel = CombShieldLevel.MEADOW,
) -> Cell:
    """Build a Cell reporting this test host's own OsFamily, so tmp_path's text matches it."""
    return make_cell(
        kind=kind,
        source=source,
        access_level=access_level,
        comb_shield=comb_shield,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )


def test_decide_persist_with_no_leave_context_never_persists(tmp_path: Path) -> None:
    decision = decide_persist(None, tmp_path / "outside" / "x.txt", b"hello")

    assert decision.persist is False
    assert decision.approved_by is None
    assert decision.reason is None
    assert decision.record is None


def test_decide_persist_allow_sets_persist_and_policy_approval(tmp_path: Path) -> None:
    cell = _cell()
    leaves = (PlannedLeaving(pattern="~/keep.txt", reason="the goal asked for it"),)
    leave = build_leave_context(cell, load_leave_policy(), leaves, None, tmp_path)

    decision = decide_persist(leave, tmp_path / "keep.txt", b"hello")

    assert decision.persist is True
    assert decision.approved_by is ApprovedBy.POLICY
    assert decision.reason == "the goal asked for it"
    assert decision.record is not None
    assert decision.record.verdict is LeaveVerdict.ALLOW
    assert decision.record.persisted is True
    assert decision.record.path == str(tmp_path / "keep.txt")


def test_decide_persist_undeclared_path_denies_and_never_sets_approval(tmp_path: Path) -> None:
    cell = _cell()
    leave = build_leave_context(cell, load_leave_policy(), (), None, tmp_path)

    decision = decide_persist(leave, tmp_path / "never-declared.txt", b"hello")

    assert decision.persist is False
    assert decision.approved_by is None
    assert decision.reason is None
    assert decision.record is not None
    assert decision.record.verdict is LeaveVerdict.DENY
    assert decision.record.persisted is False


def test_decide_persist_scratch_access_denies_even_when_declared(tmp_path: Path) -> None:
    cell = _cell(access_level=AccessLevel.SCRATCH)
    leaves = (PlannedLeaving(pattern="~/keep.txt", reason="wanted"),)
    leave = build_leave_context(cell, load_leave_policy(), leaves, None, tmp_path)

    decision = decide_persist(leave, tmp_path / "keep.txt", b"hello")

    assert decision.persist is False
    assert decision.record is not None
    assert decision.record.verdict is LeaveVerdict.DENY


def test_decide_persist_night_veil_denies(tmp_path: Path) -> None:
    cell = _cell(kind=CellKind.VIRTUAL, source="hive", comb_shield=CombShieldLevel.NIGHT_VEIL)
    leaves = (PlannedLeaving(pattern="~/keep.txt", reason="wanted"),)
    leave = build_leave_context(cell, load_leave_policy(), leaves, None, tmp_path)

    decision = decide_persist(leave, tmp_path / "keep.txt", b"hello")

    assert decision.persist is False
    assert decision.record is not None
    assert decision.record.verdict is LeaveVerdict.DENY


def test_build_leave_context_reads_is_hive_stand_from_cell_source_not_kind(tmp_path: Path) -> None:
    borrowed = _cell(source="swarm")
    leaves = (PlannedLeaving(pattern="~/keep.txt", reason="wanted"),)

    leave = build_leave_context(borrowed, load_leave_policy(), leaves, None, tmp_path)

    assert leave.cell.is_hive_stand is False


def test_build_leave_context_keep_root_none_by_default(tmp_path: Path) -> None:
    cell = _cell()

    leave = build_leave_context(cell, load_leave_policy(), (), None, tmp_path)

    assert leave.keep_root is None


def test_build_leave_context_uses_the_cells_own_os_family(tmp_path: Path) -> None:
    cell = _cell()

    leave = build_leave_context(cell, load_leave_policy(), (), None, tmp_path)

    assert leave.os_family is _HOST_OS_FAMILY
