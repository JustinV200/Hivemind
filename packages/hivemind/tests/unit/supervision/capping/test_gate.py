"""Unit tests for hivemind.supervision.capping.gate: CappingGate's propose/get/pending/run."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from builders.capping import FakeLeaseView, make_action, make_postcondition, make_proposal
from builders.cells import make_cell, make_identity

from hivemind.cell import (
    Cell,
    CellKind,
    CompletedCommand,
    FakeSession,
    NoopSnapshotter,
    SnapshotId,
    Snapshotter,
)
from hivemind.guard import CapabilitySet
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.supervision.capping.checks.deterministic import deterministic_checks
from hivemind.supervision.capping.errors import UnknownProposalError
from hivemind.supervision.capping.gate import CappingGate, GateDeps
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable
from waggle.clock import FakeClock
from waggle.ids import MessageId
from waggle.messages.capping import ActionKind, CheckKind
from waggle.messages.labels import PostconditionKind


def _deps(
    tmp_path: Path,
    tiers: TierTable,
    *,
    responder: dict[str, CompletedCommand] | None = None,
    allowed_paths: tuple[Path, ...] = (),
) -> tuple[GateDeps, MemoryPheromoneTrail, FakeSession]:
    """Build a GateDeps scoped to a fresh scratch dir, plus the trail and session for assertions."""
    scratch_root = tmp_path / "scratch"
    clock = FakeClock()
    session = FakeSession(scratch_root, clock, responder=responder, allowed_paths=allowed_paths)
    trail = MemoryPheromoneTrail(clock)
    deps = GateDeps(
        session=session,
        snapshotter=NoopSnapshotter(),
        cell=make_cell(kind=CellKind.REAL, clock=clock),
        tiers=tiers,
        trail=trail,
        identity=make_identity(clock),
        clock=clock,
        checks=deterministic_checks(),
    )
    return deps, trail, session


def _with_virtual_snapshotter(deps: GateDeps, snapshotter: Snapshotter) -> GateDeps:
    """Return `deps` with a VIRTUAL Cell and `snapshotter` swapped in.

    Codingrules 5.1: `_deps` itself stays at its own five-parameter limit; roadmap 5.10's tests
    below build on top of it with `dataclasses.replace` instead of growing `_deps` further.
    """
    return dataclasses.replace(
        deps, snapshotter=snapshotter, cell=make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
    )


@dataclass
class _RecordingSnapshotter:
    """A test-only Snapshotter, local to this module (codingrules 14.4: fakes live in `src/`).

    Records every `snapshot`/`rollback` call and always succeeds, so a test can assert the gate
    called it with the right Cell and the right SnapshotId, proving the whole-Cell rollback path
    ADR-0018 describes ("a Virtual Cell's proposals get whole-Cell rollback automatically once a
    real Snapshotter is wired in").
    """

    next_id: SnapshotId = field(default_factory=lambda: SnapshotId("snap_recording_1"))
    snapshot_calls: list[Cell] = field(default_factory=list)
    rollback_calls: list[tuple[Cell, SnapshotId]] = field(default_factory=list)

    async def snapshot(self, cell: Cell) -> SnapshotId:
        self.snapshot_calls.append(cell)
        return self.next_id

    async def rollback(self, cell: Cell, snapshot: SnapshotId) -> None:
        self.rollback_calls.append((cell, snapshot))


def _scratch_write_tiers(**overrides: object) -> TierTable:
    fields: dict[str, object] = {
        "tiers": {
            RiskTier.SCRATCH_WRITE: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.SIZE_CAP),
                floor=(CheckKind.SCHEMA,),
                snapshot_before=False,
                max_diff_bytes=1_048_576,
            ),
        }
    }
    fields.update(overrides)
    return TierTable(**fields)


async def _trail_kinds(trail: MemoryPheromoneTrail, proposal_id: MessageId) -> list[str]:
    events = await trail.query(TrailQuery(subject_id=proposal_id))
    return [event.kind for event in events]


# ──────────────────────────────────────────────────────────────────────────────
# Happy path: DIFF in scratch
# ──────────────────────────────────────────────────────────────────────────────


async def test_gate_run_diff_in_scratch_verifies_with_events_in_order(tmp_path: Path) -> None:
    deps, trail, _session = _deps(tmp_path, _scratch_write_tiers())
    gate = CappingGate(deps)
    proposal = make_proposal(
        risk_tier=RiskTier.SCRATCH_WRITE
    )  # DIFF -> note.txt; FILE_EXISTS check.
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.VERIFIED
    assert all(pc.has_held for pc in outcome.postconditions)
    kinds = await _trail_kinds(trail, proposal_id)
    assert kinds == [
        "capping.proposed",
        "capping.checked",  # SCHEMA
        "capping.checked",  # SIZE_CAP
        "capping.capped",
        "capping.applied",
        "capping.verified",
    ]


async def test_gate_run_records_only_ids_tiers_and_outcomes_on_the_trail(tmp_path: Path) -> None:
    deps, trail, _session = _deps(tmp_path, _scratch_write_tiers())
    gate = CappingGate(deps)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    await gate.run(proposal_id, CapabilitySet.parse(), lease)

    events = await trail.query(TrailQuery(subject_id=proposal_id))
    for event in events:
        assert "reason" not in event.payload  # The human-readable reason never reaches the trail.


# ──────────────────────────────────────────────────────────────────────────────
# COMMAND path
# ──────────────────────────────────────────────────────────────────────────────


async def test_gate_run_command_verifies_on_zero_exit(tmp_path: Path) -> None:
    tiers = TierTable(
        tiers={
            RiskTier.DEVICE_COMMAND: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST),
                floor=(CheckKind.SCHEMA,),
                snapshot_before=True,
            ),
        }
    )
    deps, trail, _session = _deps(
        tmp_path,
        tiers,
        responder={"true": CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    gate = CappingGate(deps)
    proposal = make_proposal(
        risk_tier=RiskTier.DEVICE_COMMAND,
        action=make_action(ActionKind.COMMAND, command=("true",)),
        postconditions=(),
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse("exec:true"), lease)

    assert outcome.state is ProposalState.VERIFIED
    kinds = await _trail_kinds(trail, proposal_id)
    assert kinds[-1] == "capping.verified"


async def test_gate_run_command_rolls_back_on_nonzero_exit(tmp_path: Path) -> None:
    tiers = TierTable(
        tiers={
            RiskTier.DEVICE_COMMAND: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST), floor=(CheckKind.SCHEMA,)
            ),
        }
    )
    deps, trail, _session = _deps(
        tmp_path,
        tiers,
        responder={"false": CompletedCommand(exit_code=1, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    gate = CappingGate(deps)
    proposal = make_proposal(
        risk_tier=RiskTier.DEVICE_COMMAND,
        action=make_action(ActionKind.COMMAND, command=("false",)),
        postconditions=(),
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse("exec:false"), lease)

    assert outcome.state is ProposalState.ROLLED_BACK
    kinds = await _trail_kinds(trail, proposal_id)
    assert kinds[-1] == "capping.rolled_back"


# ──────────────────────────────────────────────────────────────────────────────
# REJECTED outcomes
# ──────────────────────────────────────────────────────────────────────────────


async def test_gate_run_rejects_a_missing_capability(tmp_path: Path) -> None:
    tiers = TierTable(
        tiers={
            RiskTier.OUTSIDE_SCRATCH_WRITE: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST, CheckKind.SIZE_CAP),
                floor=(CheckKind.SCHEMA,),
                max_diff_bytes=1_048_576,
            ),
        }
    )
    scratch_root = tmp_path / "scratch"
    deps, _trail, _session = _deps(tmp_path, tiers)
    gate = CappingGate(deps)
    action = make_action(ActionKind.DIFF, paths=("note.txt",))
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)
    lease = FakeLeaseView(scratch_root)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)  # No fs:write capability.

    assert outcome.state is ProposalState.REJECTED


async def test_gate_run_rejects_a_path_outside_scratch_not_allowed(tmp_path: Path) -> None:
    tiers = TierTable(
        tiers={
            RiskTier.OUTSIDE_SCRATCH_WRITE: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST, CheckKind.SIZE_CAP),
                floor=(CheckKind.SCHEMA,),
                max_diff_bytes=1_048_576,
            ),
        }
    )
    deps, _trail, _session = _deps(tmp_path, tiers)
    gate = CappingGate(deps)
    outside = tmp_path / "elsewhere" / "config.toml"
    action = make_action(ActionKind.DIFF, paths=(str(outside),))
    proposal = make_proposal(risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE, action=action)
    lease = FakeLeaseView(deps.session.scratch_dir)  # No allowed_paths: `outside` is unreachable.
    capabilities = CapabilitySet.parse(f"fs:write:{outside.as_posix()}")

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, capabilities, lease)

    assert outcome.state is ProposalState.REJECTED


async def test_gate_run_rejects_an_unavailable_required_check(tmp_path: Path) -> None:
    tiers = TierTable(
        tiers={
            RiskTier.SCRATCH_WRITE: TierSpec(checks=(CheckKind.LINT,), floor=(CheckKind.LINT,)),
        }
    )
    deps, _trail, _session = _deps(tmp_path, tiers)  # deterministic_checks() has no LINT entry.
    gate = CappingGate(deps)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.REJECTED
    assert outcome.reason == "check unavailable"


async def test_gate_run_rejects_action_sequence(tmp_path: Path) -> None:
    deps, _trail, _session = _deps(tmp_path, _scratch_write_tiers())
    gate = CappingGate(deps)
    proposal = make_proposal(
        risk_tier=RiskTier.SCRATCH_WRITE, action=make_action(ActionKind.ACTION_SEQUENCE)
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.REJECTED
    assert "unsupported in v0" in outcome.reason


async def test_gate_run_rejects_an_unconfigured_tier(tmp_path: Path) -> None:
    deps, _trail, _session = _deps(tmp_path, TierTable(tiers={}))
    gate = CappingGate(deps)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.REJECTED
    assert outcome.reason == "tier not configured"


# ──────────────────────────────────────────────────────────────────────────────
# ROLLED_BACK: a failed postcondition
# ──────────────────────────────────────────────────────────────────────────────


async def test_gate_run_rolled_back_deletes_a_created_file(tmp_path: Path) -> None:
    deps, _trail, session = _deps(tmp_path, _scratch_write_tiers())
    gate = CappingGate(deps)
    action = make_action(ActionKind.DIFF, paths=("note.txt",), diff="@@ -0,0 +1,1 @@\n+hi\n")
    proposal = make_proposal(
        risk_tier=RiskTier.SCRATCH_WRITE,
        action=action,
        postconditions=(make_postcondition(PostconditionKind.FILE_EXISTS, subject="other.txt"),),
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert outcome.postconditions[0].has_held is False
    with pytest.raises(FileNotFoundError):
        await session.get_file(Path("note.txt"))  # The created file was deleted on rollback.


async def test_gate_run_rolled_back_restores_prior_bytes(tmp_path: Path) -> None:
    deps, _trail, session = _deps(tmp_path, _scratch_write_tiers())
    await session.put_file(Path("note.txt"), b"before")
    gate = CappingGate(deps)
    action = make_action(
        ActionKind.DIFF, paths=("note.txt",), diff="@@ -1,1 +1,1 @@\n-before\n+after\n"
    )
    proposal = make_proposal(
        risk_tier=RiskTier.SCRATCH_WRITE,
        action=action,
        postconditions=(make_postcondition(PostconditionKind.FILE_ABSENT, subject="note.txt"),),
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse(), lease)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert await session.get_file(Path("note.txt")) == b"before"


# ──────────────────────────────────────────────────────────────────────────────
# Roadmap step 5.10: snapshot_before + GateDeps.snapshotter already do the whole job (gate.py
# itself is unchanged by this dispatch -- see its own report); these tests prove that end to end
# with a recording fake Snapshotter, and that SnapshotUnsupportedError falls back to REVERSE_DIFF.
# ──────────────────────────────────────────────────────────────────────────────


def _snapshot_before_tiers(**overrides: object) -> TierTable:
    """A DEVICE_COMMAND tier with snapshot_before=True, matching the shipped table's own value."""
    fields: dict[str, object] = {
        "tiers": {
            RiskTier.DEVICE_COMMAND: TierSpec(
                checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST),
                floor=(CheckKind.SCHEMA,),
                snapshot_before=True,
            ),
        }
    }
    fields.update(overrides)
    return TierTable(**fields)


async def test_gate_run_snapshots_before_applying_when_the_tier_requires_it(
    tmp_path: Path,
) -> None:
    """`_check_and_cap` calls `snapshotter.snapshot(cell)` once, before apply, for this tier."""
    snapshotter = _RecordingSnapshotter()
    base, _trail, _session = _deps(
        tmp_path,
        _snapshot_before_tiers(),
        responder={"true": CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    deps = _with_virtual_snapshotter(base, snapshotter)
    gate = CappingGate(deps)
    proposal = make_proposal(
        risk_tier=RiskTier.DEVICE_COMMAND,
        action=make_action(ActionKind.COMMAND, command=("true",)),
        postconditions=(),
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse("exec:true"), lease)

    assert outcome.state is ProposalState.VERIFIED
    assert snapshotter.snapshot_calls == [deps.cell]
    assert snapshotter.rollback_calls == []  # Verified: never needed a rollback.


async def test_gate_run_rolls_back_the_whole_cell_via_the_recording_snapshotter(
    tmp_path: Path,
) -> None:
    """A failed postcondition rolls back through `snapshotter.rollback(cell, snapshot_id)`."""
    snapshotter = _RecordingSnapshotter()
    base, trail, _session = _deps(
        tmp_path,
        _snapshot_before_tiers(),
        responder={"true": CompletedCommand(exit_code=0, stdout=b"", stderr=b"", duration_s=0.0)},
    )
    deps = _with_virtual_snapshotter(base, snapshotter)
    gate = CappingGate(deps)
    proposal = make_proposal(
        risk_tier=RiskTier.DEVICE_COMMAND,
        action=make_action(ActionKind.COMMAND, command=("true",)),
        postconditions=(make_postcondition(PostconditionKind.FILE_EXISTS, subject="missing.txt"),),
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, CapabilitySet.parse("exec:true"), lease)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert snapshotter.snapshot_calls == [deps.cell]
    assert snapshotter.rollback_calls == [(deps.cell, snapshotter.next_id)]
    events = await trail.query(TrailQuery(subject_id=proposal_id))
    rolled_back = next(e for e in events if e.kind == "capping.rolled_back")
    assert rolled_back.payload["method"] == "SNAPSHOT"


async def test_gate_run_falls_back_to_reverse_diff_when_the_snapshotter_is_unsupported(
    tmp_path: Path,
) -> None:
    """ADR-0018: NoopSnapshotter.rollback raises SnapshotUnsupportedError, caught by the gate.

    The gate restores file by file instead, even though the tier itself asked for
    snapshot_before.
    """
    deps, trail, session = _deps(tmp_path, _snapshot_before_tiers())
    await session.put_file(Path("note.txt"), b"before")
    gate = CappingGate(deps)
    action = make_action(
        ActionKind.DIFF, paths=("note.txt",), diff="@@ -1,1 +1,1 @@\n-before\n+after\n"
    )
    proposal = make_proposal(
        risk_tier=RiskTier.DEVICE_COMMAND,
        action=action,
        postconditions=(make_postcondition(PostconditionKind.FILE_ABSENT, subject="note.txt"),),
    )
    lease = FakeLeaseView(deps.session.scratch_dir)
    # DEVICE_COMMAND's own checks include ALLOWLIST (unlike SCRATCH_WRITE), so a DIFF touching a
    # scratch path still needs an explicit fs:write grant for it, mirroring the
    # outside-scratch-write tests above.
    resolved = (deps.session.scratch_dir / "note.txt").resolve()
    capabilities = CapabilitySet.parse(f"fs:write:{resolved.as_posix()}")

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, capabilities, lease)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert await session.get_file(Path("note.txt")) == b"before"
    events = await trail.query(TrailQuery(subject_id=proposal_id))
    rolled_back = next(e for e in events if e.kind == "capping.rolled_back")
    assert rolled_back.payload["method"] == "REVERSE_DIFF"


# ──────────────────────────────────────────────────────────────────────────────
# propose / get / pending
# ──────────────────────────────────────────────────────────────────────────────


async def test_gate_get_raises_for_an_unknown_proposal(tmp_path: Path) -> None:
    deps, _trail, _session = _deps(tmp_path, _scratch_write_tiers())
    gate = CappingGate(deps)

    with pytest.raises(UnknownProposalError):
        gate.get(MessageId("msg_does_not_exist"))


async def test_gate_pending_excludes_terminal_proposals(tmp_path: Path) -> None:
    deps, _trail, _session = _deps(tmp_path, _scratch_write_tiers())
    gate = CappingGate(deps)
    verified_proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)
    still_pending = make_proposal(
        risk_tier=RiskTier.SCRATCH_WRITE, action=make_action(paths=("other.txt",))
    )
    lease = FakeLeaseView(deps.session.scratch_dir)

    verified_id = await gate.propose(verified_proposal)
    pending_id = await gate.propose(still_pending)
    await gate.run(verified_id, CapabilitySet.parse(), lease)

    pending_ids = {p.id for p in gate.pending()}
    assert pending_ids == {pending_id}


async def test_gate_propose_records_capping_proposed(tmp_path: Path) -> None:
    deps, trail, _session = _deps(tmp_path, _scratch_write_tiers())
    gate = CappingGate(deps)
    proposal = make_proposal(risk_tier=RiskTier.SCRATCH_WRITE)

    proposal_id = await gate.propose(proposal)

    events = await trail.query(TrailQuery(subject_id=proposal_id))
    assert len(events) == 1
    assert events[0].kind == "capping.proposed"
    assert events[0].payload["tier"] == RiskTier.SCRATCH_WRITE.value
