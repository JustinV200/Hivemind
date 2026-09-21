"""Unit tests for apply_action's COMMAND scan (roadmap step 5.0e): appeared, changed, unchanged.

`run_command` effects cannot carry a restore record the way a DIFF or COPY's own target path can,
so `hivemind.supervision.capping.apply._apply_command` scans the task's declared `leaves` patterns
before and after each command and ledgers what appeared or changed there (module docstring of
`hivemind.supervision.capping.apply`). These tests drive that scan through a scripted `FakeSession`
responder that writes real files to `tmp_path` as a command's own side effect, since the scan
itself walks the real filesystem (roadmap phase 5 runs on the Hive Stand alone).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from builders.capping import FakeLeaseView, make_action, make_proposal
from builders.cells import make_capabilities, make_cell

from hivemind.cell import AccessLevel, CellKind, CompletedCommand, ExecSpec, FakeSession, OsFamily
from hivemind.supervision.capping.apply import ApplyExtras, apply_action
from hivemind.supervision.capping.leave.persist import LeaveApplyContext, build_leave_context
from hivemind.supervision.capping.leave.scan import MAX_SCAN_FILES
from hivemind.supervision.capping.leave.table import load_leave_policy
from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.tiers import RiskTier
from waggle.clock import FakeClock
from waggle.messages import PlannedLeaving
from waggle.messages.capping import ActionKind

_HOST_OS_FAMILY = OsFamily.WINDOWS if os.name == "nt" else OsFamily.LINUX
_OK = CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)


def _gate_leave_context(cell_home: Path, declared: tuple[PlannedLeaving, ...]) -> LeaveApplyContext:
    """Build a LeaveApplyContext at FULL access on the (simulated) Hive Stand."""
    cell = make_cell(
        kind=CellKind.REAL,
        source="hive_stand",
        access_level=AccessLevel.FULL,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )
    return build_leave_context(cell, load_leave_policy(), declared, None, cell_home)


def _command_proposal(
    responder: Callable[[ExecSpec], CompletedCommand],
) -> tuple[FakeSession, Proposal]:
    """Build a FakeSession scripted with `responder` and a COMMAND proposal to run through it."""
    session = FakeSession(Path("scratch"), FakeClock(), responder=responder)
    action = make_action(ActionKind.COMMAND, command=("build",))
    return session, make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)


async def test_command_ledgers_a_file_that_appeared_under_a_declared_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    declared_dir = home / "project"
    declared_dir.mkdir(parents=True)

    def responder(spec: ExecSpec) -> CompletedCommand:
        (declared_dir / "output.txt").write_bytes(b"new file")
        return _OK

    session, proposal = _command_proposal(responder)
    lease = FakeLeaseView(Path("scratch"), allowed_paths=(declared_dir,))
    leaves = (PlannedLeaving(pattern=str(declared_dir), reason="kept for the test"),)
    leave = _gate_leave_context(home, leaves)

    result = await apply_action(session, lease, proposal, Path("scratch"), ApplyExtras(leave=leave))

    assert result.succeeded
    assert len(result.leave_decisions) == 1
    resolved = (declared_dir / "output.txt").resolve(strict=False)
    assert lease.restore_records == [(resolved, None)]  # Appeared: nothing there before.


async def test_command_ledgers_a_file_that_changed_under_a_declared_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    declared_dir = home / "project"
    declared_dir.mkdir(parents=True)
    existing = declared_dir / "config.txt"
    existing.write_bytes(b"before")

    def responder(spec: ExecSpec) -> CompletedCommand:
        existing.write_bytes(b"after")
        return _OK

    session, proposal = _command_proposal(responder)
    lease = FakeLeaseView(Path("scratch"), allowed_paths=(declared_dir,))
    leaves = (PlannedLeaving(pattern=str(declared_dir), reason="kept for the test"),)
    leave = _gate_leave_context(home, leaves)

    result = await apply_action(session, lease, proposal, Path("scratch"), ApplyExtras(leave=leave))

    assert result.succeeded
    resolved = existing.resolve(strict=False)
    assert lease.restore_records == [(resolved, b"before")]  # Changed: prior content carried over.


async def test_command_does_not_ledger_an_unchanged_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    declared_dir = home / "project"
    declared_dir.mkdir(parents=True)
    (declared_dir / "untouched.txt").write_bytes(b"same before and after")

    def responder(spec: ExecSpec) -> CompletedCommand:
        return _OK  # The command runs but touches nothing.

    session, proposal = _command_proposal(responder)
    lease = FakeLeaseView(Path("scratch"), allowed_paths=(declared_dir,))
    leaves = (PlannedLeaving(pattern=str(declared_dir), reason="kept for the test"),)
    leave = _gate_leave_context(home, leaves)

    result = await apply_action(session, lease, proposal, Path("scratch"), ApplyExtras(leave=leave))

    assert result.succeeded
    assert result.leave_decisions == ()
    assert lease.restore_records == []


async def test_command_ignores_a_file_outside_every_declared_root(tmp_path: Path) -> None:
    home = tmp_path / "home"
    declared_dir = home / "project"
    declared_dir.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def responder(spec: ExecSpec) -> CompletedCommand:
        (elsewhere / "not-declared.txt").write_bytes(b"never scanned")
        return _OK

    session, proposal = _command_proposal(responder)
    lease = FakeLeaseView(Path("scratch"), allowed_paths=(declared_dir, elsewhere))
    leaves = (PlannedLeaving(pattern=str(declared_dir), reason="kept for the test"),)
    leave = _gate_leave_context(home, leaves)

    result = await apply_action(session, lease, proposal, Path("scratch"), ApplyExtras(leave=leave))

    assert result.succeeded
    assert result.leave_decisions == ()
    assert lease.restore_records == []


async def test_command_scan_is_a_noop_with_no_leave_context(tmp_path: Path) -> None:
    home = tmp_path / "home"
    declared_dir = home / "project"
    declared_dir.mkdir(parents=True)

    def responder(spec: ExecSpec) -> CompletedCommand:
        (declared_dir / "output.txt").write_bytes(b"new file")
        return _OK

    session, proposal = _command_proposal(responder)
    lease = FakeLeaseView(Path("scratch"))

    result = await apply_action(session, lease, proposal, Path("scratch"))

    assert result.succeeded
    assert result.leave_decisions == ()
    assert lease.restore_records == []


async def test_command_scan_stops_at_the_file_cap(tmp_path: Path) -> None:
    home = tmp_path / "home"
    declared_dir = home / "project"
    declared_dir.mkdir(parents=True)

    def responder(spec: ExecSpec) -> CompletedCommand:
        for i in range(MAX_SCAN_FILES + 5):
            (declared_dir / f"file-{i}.txt").write_bytes(b"x")
        return _OK

    session, proposal = _command_proposal(responder)
    lease = FakeLeaseView(Path("scratch"), allowed_paths=(declared_dir,))
    leaves = (PlannedLeaving(pattern=str(declared_dir), reason="kept for the test"),)
    leave = _gate_leave_context(home, leaves)

    result = await apply_action(session, lease, proposal, Path("scratch"), ApplyExtras(leave=leave))

    assert result.succeeded
    assert len(result.leave_decisions) == MAX_SCAN_FILES
