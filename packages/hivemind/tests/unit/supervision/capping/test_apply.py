"""Unit tests for hivemind.supervision.capping.apply: apply_action's DIFF and COMMAND paths."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from builders.capping import FakeLeaseView, make_action, make_proposal

from hivemind.cell import CompletedCommand, FakeSession, Responder
from hivemind.supervision.capping.apply import ApplyExtras, apply_action
from hivemind.supervision.capping.errors import CappingError
from hivemind.supervision.capping.tiers import RiskTier
from waggle.clock import FakeClock
from waggle.messages.capping import ActionKind, ProposedAction


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


async def test_apply_action_applies_a_network_step_as_a_no_op(tmp_path: Path) -> None:
    # Roadmap step 10.3: the HTTP tool's step is authorised by the gate, then sent by the tool.
    scratch_root = tmp_path / "scratch"
    lease = FakeLeaseView(scratch_root)
    action = make_action(ActionKind.ACTION_SEQUENCE, steps=("GET https://example.com/",))
    proposal = make_proposal(risk_tier=RiskTier.NETWORK_EGRESS, action=action)

    result = await apply_action(_session(scratch_root), lease, proposal, scratch_root)

    assert result.succeeded
    assert result.touched == ()
    assert lease.touched_paths == []


async def test_apply_action_rejects_action_sequence(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    session = _session(scratch_root)
    lease = FakeLeaseView(scratch_root)
    action = make_action(ActionKind.ACTION_SEQUENCE)
    proposal = make_proposal(risk_tier=RiskTier.DEVICE_COMMAND, action=action)

    with pytest.raises(CappingError):
        await apply_action(session, lease, proposal, scratch_root)


# ──────────────────────────────────────────────────────────────────────────────
# COPY (roadmap step 5.0e, the `keep` tool)
# ──────────────────────────────────────────────────────────────────────────────


def _copy_action(source: str, destination: Path, content: bytes) -> ProposedAction:
    """Build a valid COPY ProposedAction, its sha256/size matching `content` for real."""
    return make_action(
        ActionKind.COPY,
        paths=(source, str(destination)),
        copy_sha256=hashlib.sha256(content).hexdigest(),
        copy_size=len(content),
    )


async def test_apply_action_copy_moves_the_source_to_the_destination(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    await session.put_file(Path("installer.exe"), b"\x00binary\x01content")
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    destination = outside_dir / "installer.exe"
    action = _copy_action("installer.exe", destination, b"\x00binary\x01content")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    assert result.succeeded
    assert await session.get_file(destination) == b"\x00binary\x01content"
    with pytest.raises(FileNotFoundError):
        await session.get_file(Path("installer.exe"))  # The source is removed: keep() moves it.


async def test_apply_action_copy_records_a_restore_record_with_no_prior(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    await session.put_file(Path("installer.exe"), b"payload")
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    destination = outside_dir / "installer.exe"
    action = _copy_action("installer.exe", destination, b"payload")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    await apply_action(session, lease, proposal, scratch_root)

    resolved = destination.resolve(strict=False)
    assert lease.restore_records == [(resolved, None)]  # Nothing was there before this apply.


async def test_apply_action_copy_records_a_restore_record_with_the_priors_content(
    tmp_path: Path,
) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    await session.put_file(Path("installer.exe"), b"new payload")
    destination = outside_dir / "installer.exe"
    await session.put_file(destination, b"old payload")
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    action = _copy_action("installer.exe", destination, b"new payload")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    await apply_action(session, lease, proposal, scratch_root)

    resolved = destination.resolve(strict=False)
    assert lease.restore_records == [(resolved, b"old payload")]
    assert await session.get_file(destination) == b"new payload"


async def test_apply_action_copy_fails_closed_on_a_hash_mismatch(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    # The content on disk no longer matches the sha256/size the proposal declared.
    await session.put_file(Path("installer.exe"), b"changed since the tool proposed this")
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    destination = outside_dir / "installer.exe"
    action = _copy_action("installer.exe", destination, b"original bytes")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    assert not result.succeeded
    assert result.failure_reason is not None
    assert "no longer matches" in result.failure_reason
    with pytest.raises(FileNotFoundError):
        await session.get_file(destination)  # Nothing was ever written.


async def test_apply_action_copy_fails_closed_on_a_missing_source(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    destination = outside_dir / "installer.exe"
    action = _copy_action("never-written.exe", destination, b"content")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(session, lease, proposal, scratch_root)

    assert not result.succeeded
    assert result.failure_reason is not None
    assert "no file at" in result.failure_reason


async def test_apply_action_copy_refuses_when_the_disk_reserve_would_break(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    await session.put_file(Path("installer.exe"), b"payload")
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    destination = outside_dir / "installer.exe"
    action = _copy_action("installer.exe", destination, b"payload")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)
    # No real disk has anywhere near this many megabytes free, so the reserve always breaks.
    huge_reserve_mb = 2**40

    result = await apply_action(
        session, lease, proposal, scratch_root, ApplyExtras(disk_reserve_mb=huge_reserve_mb)
    )

    assert not result.succeeded
    assert result.failure_reason is not None
    assert "reserve" in result.failure_reason
    with pytest.raises(FileNotFoundError):
        await session.get_file(destination)


async def test_apply_action_copy_skips_the_disk_reserve_check_when_none(tmp_path: Path) -> None:
    scratch_root = tmp_path / "scratch"
    outside_dir = tmp_path / "outside"
    session = _session(scratch_root, allowed_paths=(outside_dir,))
    await session.put_file(Path("installer.exe"), b"payload")
    lease = FakeLeaseView(scratch_root, allowed_paths=(outside_dir,))
    destination = outside_dir / "installer.exe"
    action = _copy_action("installer.exe", destination, b"payload")
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)

    result = await apply_action(
        session, lease, proposal, scratch_root, ApplyExtras(disk_reserve_mb=None)
    )

    assert result.succeeded
