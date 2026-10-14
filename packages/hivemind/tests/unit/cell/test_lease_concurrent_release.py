"""Unit tests for hivemind.cell.lease: release() with more than one caller, or a cancelled one.

Fits into the Hive:
    Mirrors src/hivemind/cell/lease.py (codingrules section 3), split by feature (14.2) from
    test_lease.py, which holds the lifecycle one caller at a time. A Warden's `stop()` can race its
    own idle release of the lease (a device's Warden gives it back whenever it settles with nothing
    to supervise), and a shutdown can cancel the tick that was releasing it. Neither may leave the
    lease where the other has no legal edge to take, or make `stop()` raise.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.lease for the module under test.
    - hivemind.cell.lease_state for the edges these tests walk (RELEASING has none back to itself).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cells import make_identity, make_lease_request

from hivemind.cell.lease import LeaseFacts, LeaseReleaseReport, RealCellLease
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.tiers import CombShieldLevel
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_lease_id

_WAIT_S = 5.0  # A hung release fails the test instead of blocking the suite.


class _GatedReleaser:
    """A releaser that holds each release at the Cell until the test says it may finish.

    Records how many releases began, and fails the first `failures` of them once the gate opens, so
    one test can hold a release in flight, queue a second caller behind it, and choose how it ends.
    """

    def __init__(self, failures: int = 0) -> None:
        self.calls = 0
        self.entered = asyncio.Event()  # Set when a release has reached the Cell.
        self.gate = asyncio.Event()  # Opened by the test: the Cell answers.
        self._failures = failures

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        self.calls += 1
        self.entered.set()
        await self.gate.wait()
        if self.calls <= self._failures:
            raise OSError("the device did not answer")
        return LeaseReleaseReport(killed_processes=1, residual_paths=(), is_restored=True)


async def _opened(
    tmp_path: Path, releaser: _GatedReleaser
) -> tuple[RealCellLease, MemoryPheromoneTrail]:
    """An OPEN lease over `tmp_path` whose release goes through `releaser`."""
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    request = make_lease_request(clock=clock)
    facts = LeaseFacts(
        id=new_lease_id(clock),
        cell_id=request.cell_id,
        holder=request.holder,
        task_id=None,
        scratch_root=tmp_path,
        access_level=request.access_level,
        comb_shield=CombShieldLevel.MEADOW,
        allowed_paths=(),
    )
    lease = RealCellLease(
        facts, trail=trail, clock=clock, identity=make_identity(clock=clock), releaser=releaser
    )
    await lease.open()
    return lease, trail


async def _in_flight(
    lease: RealCellLease, releaser: _GatedReleaser
) -> asyncio.Task[LeaseReleaseReport]:
    """Start a release and wait until it is at the Cell, so a second caller finds it in flight."""
    first = asyncio.ensure_future(lease.release())
    await asyncio.wait_for(releaser.entered.wait(), timeout=_WAIT_S)
    return first


def _state_of(lease: RealCellLease) -> LeaseState:
    """Read `lease.state` through a call, so mypy does not carry an earlier narrowing over it."""
    return lease.state


async def _yield_to_others() -> None:
    """Let every task that is ready run, so a caller that was going to fail has failed."""
    for _ in range(20):
        await asyncio.sleep(0)


async def test_a_second_release_waits_for_the_one_in_flight_and_returns_its_report(
    tmp_path: Path,
) -> None:
    releaser = _GatedReleaser()
    lease, trail = await _opened(tmp_path, releaser)
    first = await _in_flight(lease, releaser)

    second = asyncio.ensure_future(lease.release())  # Found RELEASING -> RELEASING, once.
    await _yield_to_others()
    assert not second.done()  # It waits: the first caller owns the release.
    releaser.gate.set()

    first_report = await asyncio.wait_for(first, timeout=_WAIT_S)
    second_report = await asyncio.wait_for(second, timeout=_WAIT_S)

    assert second_report is first_report
    assert releaser.calls == 1
    assert lease.state is LeaseState.RELEASED
    assert len(await trail.query(TrailQuery(kind="cell.released"))) == 1


async def test_a_second_release_tries_again_when_the_one_it_waited_for_failed(
    tmp_path: Path,
) -> None:
    releaser = _GatedReleaser(failures=1)
    lease, trail = await _opened(tmp_path, releaser)
    first = await _in_flight(lease, releaser)
    second = asyncio.ensure_future(lease.release())
    await _yield_to_others()

    releaser.gate.set()
    with pytest.raises(OSError, match="did not answer"):
        await asyncio.wait_for(first, timeout=_WAIT_S)
    report = await asyncio.wait_for(second, timeout=_WAIT_S)

    assert report.killed_processes == 1
    assert releaser.calls == 2  # The waiting caller did the work the failed one could not.
    assert lease.state is LeaseState.RELEASED
    assert len(await trail.query(TrailQuery(kind="cell.released"))) == 1


async def test_a_cancelled_release_leaves_the_lease_orphaned_and_releasable(
    tmp_path: Path,
) -> None:
    releaser = _GatedReleaser()
    lease, trail = await _opened(tmp_path, releaser)
    first = await _in_flight(lease, releaser)

    first.cancel()  # A shutdown cancels the tick that was giving the lease back.
    with pytest.raises(asyncio.CancelledError):
        await first
    # Not stuck RELEASING: ORPHANED -> RELEASING is a legal edge.
    assert _state_of(lease) is LeaseState.ORPHANED

    releaser.gate.set()
    report = await asyncio.wait_for(lease.release(), timeout=_WAIT_S)  # What stop() then does.

    assert report.killed_processes == 1
    assert _state_of(lease) is LeaseState.RELEASED
    assert releaser.calls == 2
    assert len(await trail.query(TrailQuery(kind="cell.released"))) == 1
