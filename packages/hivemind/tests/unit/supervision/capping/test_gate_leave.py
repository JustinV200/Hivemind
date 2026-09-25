"""Unit tests for CappingGate.run's own roadmap-5.0c leave-policy wiring: capping.leave_decided."""

from __future__ import annotations

import os
from pathlib import Path

from builders.capping import FakeLeaseView, make_action, make_postcondition, make_proposal
from builders.cells import make_capabilities, make_cell, make_identity

from hivemind.cell import AccessLevel, Cell, CellKind, FakeSession, NoopSnapshotter, OsFamily
from hivemind.guard import CapabilitySet
from hivemind.pheromone import MemoryPheromoneTrail, PheromoneEvent, TrailQuery
from hivemind.supervision.capping.checks.deterministic import deterministic_checks
from hivemind.supervision.capping.gate import CappingGate, GateDeps, GateOutcome
from hivemind.supervision.capping.tiers import RiskTier, TierSpec, TierTable
from waggle.clock import Clock, FakeClock
from waggle.messages import PlannedLeaving
from waggle.messages.capping import ActionKind, CheckKind

_HOST_OS_FAMILY = OsFamily.WINDOWS if os.name == "nt" else OsFamily.LINUX

_OUTSIDE_SCRATCH_TIERS = TierTable(
    tiers={
        RiskTier.OUTSIDE_SCRATCH_WRITE: TierSpec(
            checks=(CheckKind.SCHEMA, CheckKind.ALLOWLIST, CheckKind.SIZE_CAP),
            floor=(CheckKind.SCHEMA,),
            max_diff_bytes=1_048_576,
        ),
    }
)


def _hive_stand_cell(clock: Clock, *, access_level: AccessLevel = AccessLevel.FULL) -> Cell:
    """A REAL, FULL-access Hive Stand Cell, reporting this test host's own OsFamily."""
    return make_cell(
        kind=CellKind.REAL,
        clock=clock,
        source="hive_stand",
        access_level=access_level,
        capabilities=make_capabilities(os=_HOST_OS_FAMILY),
    )


async def _run_outside_scratch_write(
    tmp_path: Path, target_dir: str, *, declared_leaves: tuple[PlannedLeaving, ...]
) -> tuple[GateOutcome, MemoryPheromoneTrail, FakeLeaseView]:
    """Run one OUTSIDE_SCRATCH_WRITE proposal writing `target_dir/keep.txt`; return its trail."""
    scratch_root = tmp_path / "scratch"
    target_root = tmp_path / target_dir
    target = target_root / "keep.txt"
    clock = FakeClock()
    session = FakeSession(scratch_root, clock, allowed_paths=(target_root,))
    trail = MemoryPheromoneTrail(clock)
    deps = GateDeps(
        session=session,
        snapshotter=NoopSnapshotter(),
        cell=_hive_stand_cell(clock),
        tiers=_OUTSIDE_SCRATCH_TIERS,
        trail=trail,
        identity=make_identity(clock),
        clock=clock,
        checks=deterministic_checks(),
        declared_leaves=declared_leaves,
        leave_home=target_root,
    )
    gate = CappingGate(deps)
    action = make_action(ActionKind.DIFF, paths=(str(target),), diff="@@ -0,0 +1,1 @@\n+hello\n")
    proposal = make_proposal(
        risk_tier=RiskTier.OUTSIDE_SCRATCH_WRITE,
        action=action,
        postconditions=(make_postcondition(subject=str(target)),),
    )
    lease = FakeLeaseView(scratch_root, allowed_paths=(target_root,))
    resolved = target.resolve(strict=False).as_posix()
    # Roadmap step 10.3: leaving scratch needs `cell:outside_scratch` as well as `fs:write`.
    capabilities = CapabilitySet.parse(f"fs:write:{resolved}", f"cell:outside_scratch:{resolved}")

    proposal_id = await gate.propose(proposal)
    outcome = await gate.run(proposal_id, capabilities, lease)
    return outcome, trail, lease


def _leave_decided_events(
    events: tuple[PheromoneEvent, ...],
) -> list[PheromoneEvent]:
    """Filter `events` down to capping.leave_decided only."""
    return [event for event in events if event.kind == "capping.leave_decided"]


async def test_gate_run_allow_records_leave_decided_and_persists(tmp_path: Path) -> None:
    declared = (PlannedLeaving(pattern="~/keep.txt", reason="the goal asked for it"),)

    outcome, trail, lease = await _run_outside_scratch_write(
        tmp_path, "home", declared_leaves=declared
    )

    events = tuple(await trail.query(TrailQuery(subject_id=outcome.proposal_id)))
    leave_events = _leave_decided_events(events)
    assert len(leave_events) == 1
    payload = leave_events[0].payload
    assert payload["verdict"] == "ALLOW"
    assert payload["persisted"] is True
    assert "reason" not in payload  # Never the human-readable reason (codingrules section 12).
    assert lease.persist_records[0][1] is True  # persist=True was actually passed through.


async def test_gate_run_deny_records_leave_decided_and_does_not_persist(tmp_path: Path) -> None:
    # No declared_leaves at all: the hard rule from roadmap 5.0b makes this DENY.
    outcome, trail, lease = await _run_outside_scratch_write(
        tmp_path, "elsewhere", declared_leaves=()
    )

    events = tuple(await trail.query(TrailQuery(subject_id=outcome.proposal_id)))
    leave_events = _leave_decided_events(events)
    assert leave_events[0].payload["verdict"] == "DENY"
    assert leave_events[0].payload["persisted"] is False
    assert lease.persist_records[0][1] is False
