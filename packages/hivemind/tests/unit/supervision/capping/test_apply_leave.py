"""Unit tests for apply_action's roadmap-5.0c leave-policy wiring: ALLOW/DENY persist decisions.

ASK is not covered here: roadmap step 5.0c resolves it to `persist=False` (no HumanCheck exists
yet, `hivemind.supervision.capping.leave.persist`'s own module docstring); roadmap step 5.0d's own
tests cover keep/keep-for-goal/discard/timeout once the HUMAN rung is wired in.
"""

from __future__ import annotations

import os
from pathlib import Path

from builders.capping import FakeLeaseView, make_action, make_proposal
from builders.cells import make_capabilities, make_cell

from hivemind.cell import AccessLevel, CellKind, CompletedCommand, FakeSession, OsFamily, Responder
from hivemind.cell.leavings import ApprovedBy
from hivemind.supervision.capping.apply import ApplyExtras, apply_action
from hivemind.supervision.capping.leave.model import LeaveVerdict
from hivemind.supervision.capping.leave.persist import build_leave_context
from hivemind.supervision.capping.leave.table import (
    ClassPolicy,
    GeneralSettings,
    HomePolicy,
    LeavePolicyClasses,
    LeavePolicyTable,
    SingleVerdict,
    load_leave_policy,
)
from hivemind.supervision.capping.tiers import RiskTier
from waggle.clock import FakeClock
from waggle.messages import PlannedLeaving
from waggle.messages.capping import ActionKind

_HOST_OS_FAMILY = OsFamily.WINDOWS if os.name == "nt" else OsFamily.LINUX


def _session(
    scratch_root: Path,
    *,
    responder: Responder | dict[str, CompletedCommand] | None = None,
    allowed_paths: tuple[Path, ...] = (),
) -> FakeSession:
    return FakeSession(scratch_root, FakeClock(), responder=responder, allowed_paths=allowed_paths)


async def test_apply_action_leave_allow_persists_and_sets_policy_approval(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    home = tmp_path / "home"
    target = home / "keep.txt"
    session = _session(scratch_root, allowed_paths=(home,))
    lease = FakeLeaseView(scratch_root, allowed_paths=(home,))
    cell = make_cell(
        kind=CellKind.REAL,
        source="hive_stand",
        access_level=AccessLevel.FULL,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )
    leaves = (PlannedLeaving(pattern="~/keep.txt", reason="the goal asked for it"),)
    leave = build_leave_context(cell, load_leave_policy(), leaves, None, home)
    action = make_action(ActionKind.DIFF, paths=(str(target),), diff="@@ -0,0 +1,1 @@\n+hello\n")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root, ApplyExtras(leave=leave))

    assert result.succeeded
    resolved = target.resolve(strict=False)
    assert lease.persist_records == [(resolved, True, ApprovedBy.POLICY, "the goal asked for it")]
    assert len(result.leave_decisions) == 1
    assert result.leave_decisions[0].persisted is True
    # The write itself still happens, exactly as it always did, whatever the leave verdict is.
    assert await session.get_file(target) == b"hello"


async def test_apply_action_leave_undeclared_path_never_persists(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    home = tmp_path / "home"
    target = home / "never-declared.txt"
    session = _session(scratch_root, allowed_paths=(home,))
    lease = FakeLeaseView(scratch_root, allowed_paths=(home,))
    cell = make_cell(
        kind=CellKind.REAL,
        source="hive_stand",
        access_level=AccessLevel.FULL,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )
    # No declared leaves at all: roadmap 5.0b's hard rule makes this DENY regardless of anything
    # else about the path, size or Cell.
    leave = build_leave_context(cell, load_leave_policy(), (), None, home)
    action = make_action(ActionKind.DIFF, paths=(str(target),), diff="@@ -0,0 +1,1 @@\n+hello\n")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root, ApplyExtras(leave=leave))

    assert result.succeeded
    resolved = target.resolve(strict=False)
    assert lease.persist_records == [(resolved, False, None, None)]
    assert result.leave_decisions[0].persisted is False
    # Still applies, restored on release exactly as before phase 5 -- roadmap 5.0c's own rule.
    assert await session.get_file(target) == b"hello"


def _tiny_threshold_table() -> LeavePolicyTable:
    """A LeavePolicyTable with a 5-byte max_home_bytes, so a short diff is "over threshold"."""
    ask = SingleVerdict(verdict=LeaveVerdict.ASK)
    deny_pair = ClassPolicy(hive_stand_verdict=LeaveVerdict.ASK, borrowed_verdict=LeaveVerdict.DENY)
    return LeavePolicyTable(
        general=GeneralSettings(max_home_bytes=5),
        classes=LeavePolicyClasses(
            keep_root=SingleVerdict(verdict=LeaveVerdict.ALLOW),
            executable=ask,
            startup=deny_pair,
            system=deny_pair,
            other=deny_pair,
            home=HomePolicy(
                hive_stand_verdict=LeaveVerdict.ALLOW,
                borrowed_verdict=LeaveVerdict.ASK,
                over_threshold_verdict=LeaveVerdict.ASK,
            ),
        ),
    )


async def test_apply_action_leave_ask_does_not_persist_without_a_human_check(
    tmp_path: Path,
) -> None:
    """Roadmap step 5.0c: ASK falls back to persist=False until 5.0d wires the HUMAN rung."""
    scratch_root = tmp_path / "scratch"
    home = tmp_path / "home"
    # Over the (deliberately tiny) max_home_bytes threshold: always ASK, on either kind of Cell.
    target = home / "big.bin"
    session = _session(scratch_root, allowed_paths=(home,))
    lease = FakeLeaseView(scratch_root, allowed_paths=(home,))
    cell = make_cell(
        kind=CellKind.REAL,
        source="hive_stand",
        access_level=AccessLevel.FULL,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )
    leaves = (PlannedLeaving(pattern="~/big.bin", reason="a large artifact"),)
    leave = build_leave_context(cell, _tiny_threshold_table(), leaves, None, home)
    action = make_action(
        ActionKind.DIFF, paths=(str(target),), diff="@@ -0,0 +1,1 @@\n+hello world\n"
    )
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root, ApplyExtras(leave=leave))

    resolved = target.resolve(strict=False)
    assert lease.persist_records == [(resolved, False, None, None)]
    assert result.leave_decisions[0].persisted is False
    assert result.leave_decisions[0].verdict is LeaveVerdict.ASK


async def test_apply_action_with_no_leave_context_never_persists(tmp_path: Path) -> None:
    """leave=None (every call site outside roadmap phase 5's own wiring) keeps today's behaviour."""
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    target = outside_dir / "config.toml"
    action = make_action(ActionKind.DIFF, paths=(str(target),), diff="@@ -0,0 +1,1 @@\n+hi\n")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    resolved = target.resolve(strict=False)
    assert lease.persist_records == [(resolved, False, None, None)]
    assert result.leave_decisions == ()
