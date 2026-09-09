"""Unit tests for hivemind.supervision.capping.apply: apply_action's DIFF and COMMAND paths."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.capping import FakeLeaseView, make_action, make_proposal

from hivemind.cell import CompletedCommand, FakeSession, Responder
from hivemind.supervision.capping.apply import apply_action
from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.tiers import RiskTier
from waggle.clock import FakeClock
from waggle.messages.capping import ActionKind


def _session(
    scratch_root: Path,
    *,
    responder: Responder | dict[str, CompletedCommand] | None = None,
    allowed_paths: tuple[Path, ...] = (),
) -> FakeSession:
    """Build a FakeSession scoped to scratch_root, deterministic (FakeClock)."""
    return FakeSession(scratch_root, FakeClock(), responder=responder, allowed_paths=allowed_paths)


async def test_apply_action_diff_writes_a_new_file_in_scratch(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    session = _session(scratch_root)
    lease = FakeLeaseView(scratch_root)
    action = make_action(ActionKind.DIFF, paths=("note.txt",), diff="@@ -0,0 +1,1 @@\n+hello\n")
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    assert result.succeeded
    assert await session.get_file(Path("note.txt")) == b"hello"
    # Inside scratch, under a tier that is not OUTSIDE_SCRATCH_WRITE: no restore bookkeeping.
    assert lease.touched_paths == []
    assert lease.restore_records == []


async def test_apply_action_diff_outside_scratch_records_restore_before_writing(
    tmp_path: Path,
) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    target = outside_dir / "config.toml"
    action = make_action(
        ActionKind.DIFF, paths=(str(target),), diff="@@ -0,0 +1,1 @@\n+enabled = true\n"
    )
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    assert result.succeeded
    resolved = target.resolve(strict=False)
    assert lease.touched_paths == [resolved]
    assert lease.restore_records == [(resolved, None)]  # The file did not exist before.
    assert await session.get_file(target) == b"enabled = true"


async def test_apply_action_diff_records_prior_bytes_when_overwriting(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    target = outside_dir / "config.toml"
    await session.put_file(target, b"old")
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    action = make_action(
        ActionKind.DIFF, paths=(str(target),), diff="@@ -1,1 +1,1 @@\n-old\n+new\n"
    )
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    await apply_action(session, lease, proposal, scratch_root)

    resolved = target.resolve(strict=False)
    assert lease.restore_records == [(resolved, b"old")]
    assert await session.get_file(target) == b"new"


async def test_apply_action_command_reports_success_on_zero_exit(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    session = _session(
        scratch_root,
        responder={"true": CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    lease = FakeLeaseView(scratch_root)
    action = make_action(ActionKind.COMMAND, command=("true",))
    proposal = make_proposal(risk_tier=RiskTier.DEVICE_COMMAND, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    assert result.succeeded
    assert result.exit_code == 0
    assert result.touched == ()  # A COMMAND action never records a touched path.


async def test_apply_action_command_reports_failure_on_nonzero_exit(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    session = _session(
        scratch_root,
        responder={
            "false": CompletedCommand(exit_code=1, stdout=b"", stderr=b"boom", duration_s=0.0)
        },
    )
    lease = FakeLeaseView(scratch_root)
    action = make_action(ActionKind.COMMAND, command=("false",))
    proposal = make_proposal(risk_tier=RiskTier.DEVICE_COMMAND, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    assert not result.succeeded
    assert result.exit_code == 1


async def test_apply_action_rejects_action_sequence(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    session = _session(scratch_root)
    lease = FakeLeaseView(scratch_root)
    action = make_action(ActionKind.ACTION_SEQUENCE)
    proposal = make_proposal(risk_tier=RiskTier.DEVICE_COMMAND, action=action)

    with pytest.raises(CappingError):
        await apply_action(session, lease, proposal, scratch_root)
