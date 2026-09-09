"""Unit tests for hivemind.cell.lease: RealCellLease's open/release lifecycle and bookkeeping."""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cells import make_identity, make_lease_request

from hivemind.cell.errors import InvalidLeaseTransitionError
from hivemind.cell.fake import FakeLeaseReleaser
from hivemind.cell.lease import LeaseFacts, LeaseReleaser, LeaseReleaseReport, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.tiers import CombShieldLevel
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import PheromoneTrail, TrailQuery
from waggle.clock import Clock, FakeClock
from waggle.ids import new_lease_id


class _CountingReleaser:
    """A LeaseReleaser stub that counts calls and returns a fixed, overridable report."""

    def __init__(self, report: LeaseReleaseReport | None = None) -> None:
        self.calls = 0
        self._report = report or LeaseReleaseReport(
            killed_processes=1, residual_paths=(), is_restored=True
        )

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        self.calls += 1
        return self._report


def _make_lease(
    tmp_path: Path,
    allowed_paths: tuple[Path, ...] = (),
    releaser: LeaseReleaser | None = None,
    trail: PheromoneTrail | None = None,
    clock: Clock | None = None,
) -> RealCellLease:
    """Build a REQUESTED RealCellLease over `tmp_path` as its scratch root."""
    active_clock = clock if clock is not None else FakeClock()
    request = make_lease_request(clock=active_clock, allowed_paths=allowed_paths)
    facts = LeaseFacts(
        id=new_lease_id(active_clock),
        cell_id=request.cell_id,
        holder=request.holder,
        task_id=request.task_id,
        scratch_root=tmp_path,
        access_level=request.access_level,
        comb_shield=CombShieldLevel.MEADOW,
        allowed_paths=request.allowed_paths,
    )
    return RealCellLease(
        facts,
        trail=trail if trail is not None else MemoryPheromoneTrail(active_clock),
        clock=active_clock,
        identity=make_identity(clock=active_clock),
        releaser=releaser if releaser is not None else FakeLeaseReleaser(),
    )


async def test_open_transitions_to_open_and_records_cell_leased(tmp_path: Path) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    lease = _make_lease(tmp_path, trail=trail, clock=clock)

    await lease.open()

    assert lease.state is LeaseState.OPEN
    events = await trail.query(TrailQuery(kind="cell.leased"))
    assert len(events) == 1
    assert events[0].subject_id == lease.cell_id
    assert events[0].payload["lease_id"] == lease.id


async def test_open_twice_raises_invalid_lease_transition(tmp_path: Path) -> None:
    lease = _make_lease(tmp_path)
    await lease.open()

    with pytest.raises(InvalidLeaseTransitionError):
        await lease.open()


def test_note_started_process_records_every_pid(tmp_path: Path) -> None:
    lease = _make_lease(tmp_path)

    lease.note_started_process(111)
    lease.note_started_process(222)

    assert lease.started_pids == (111, 222)


async def test_note_touched_path_inside_scratch_writes_no_event(tmp_path: Path) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    lease = _make_lease(tmp_path, trail=trail, clock=clock)
    await lease.open()

    await lease.note_touched_path(tmp_path / "inside.txt")

    events = await trail.query(TrailQuery(kind="cell.touched_outside_scratch"))
    assert events == ()
    assert lease.touched_paths == ((tmp_path / "inside.txt").resolve(strict=False),)


async def test_note_touched_path_outside_scratch_records_the_event(tmp_path: Path) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    lease = _make_lease(tmp_path, trail=trail, clock=clock)
    await lease.open()
    outside = tmp_path.parent / "outside.txt"

    await lease.note_touched_path(outside)

    events = await trail.query(TrailQuery(kind="cell.touched_outside_scratch"))
    assert len(events) == 1
    assert events[0].payload["lease_id"] == lease.id


def test_is_path_allowed_true_inside_scratch_and_under_allowed_paths(tmp_path: Path) -> None:
    allowed_root = tmp_path.parent / "allowed"
    lease = _make_lease(tmp_path, allowed_paths=(allowed_root,))

    assert lease.is_path_allowed(tmp_path / "file.txt")
    assert lease.is_path_allowed(allowed_root / "file.txt")


def test_is_path_allowed_false_outside_scratch_and_allowed_paths(tmp_path: Path) -> None:
    lease = _make_lease(tmp_path)

    assert not lease.is_path_allowed(tmp_path.parent / "outside.txt")


def test_is_path_allowed_collapses_dotdot_before_checking(tmp_path: Path) -> None:
    lease = _make_lease(tmp_path)
    sneaky = tmp_path / "sub" / ".." / ".." / "outside.txt"

    assert not lease.is_path_allowed(sneaky)


async def test_release_is_idempotent_and_returns_the_first_report(tmp_path: Path) -> None:
    releaser = _CountingReleaser()
    lease = _make_lease(tmp_path, releaser=releaser)
    await lease.open()

    first = await lease.release()
    second = await lease.release()

    assert first is second
    assert releaser.calls == 1
    assert lease.state is LeaseState.RELEASED


async def test_release_delegates_to_the_injected_releaser_and_records_cell_released(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    report = LeaseReleaseReport(
        killed_processes=2, residual_paths=(Path("/left/over"),), is_restored=False
    )
    lease = _make_lease(tmp_path, releaser=_CountingReleaser(report), trail=trail, clock=clock)
    await lease.open()

    result = await lease.release()

    assert result == report
    events = await trail.query(TrailQuery(kind="cell.released"))
    assert len(events) == 1
    payload = events[0].payload
    assert payload["killed_processes"] == 2
    assert payload["residual_paths"] == 1
    assert payload["is_restored"] is False


async def test_release_before_open_raises_invalid_lease_transition(tmp_path: Path) -> None:
    lease = _make_lease(tmp_path)

    with pytest.raises(InvalidLeaseTransitionError):
        await lease.release()
