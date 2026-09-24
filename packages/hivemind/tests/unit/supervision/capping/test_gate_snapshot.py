"""Unit tests for CappingGate.run's own roadmap-5.10 whole-Cell snapshot path.

Fits into the Hive:
    Mirrors src/hivemind/supervision/capping/gate/ (codingrules section 3); split by feature
    (14.2) from test_gate.py, which covers the gate's own propose/get/pending and check outcomes,
    and from test_gate_leave.py, which covers the roadmap-5.0c leave policy.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.supervision.capping.gate.core for _check_and_cap/_apply_and_verify, under test.
    - hivemind.cell.snapshot for the Snapshotter protocol _RecordingSnapshotter stands in for.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from builders.capping import (
    FakeLeaseView,
    make_action,
    make_postcondition,
    make_proposal,
)
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
from hivemind.supervision.capping.checks import Check
from hivemind.supervision.capping.checks.deterministic import deterministic_checks
from hivemind.supervision.capping.gate import CappingGate, GateDeps
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable
from waggle.clock import FakeClock
from waggle.messages.capping import ActionKind, CheckKind
from waggle.messages.labels import PostconditionKind


def _deps(
    tmp_path: Path,
    tiers: TierTable,
    *,
    responder: dict[str, CompletedCommand] | None = None,
    allowed_paths: tuple[Path, ...] = (),
    checks: Mapping[CheckKind, Check] | None = None,
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
        checks=checks if checks is not None else deterministic_checks(),
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
