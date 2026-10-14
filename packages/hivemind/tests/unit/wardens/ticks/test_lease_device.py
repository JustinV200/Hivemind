"""Tests for hivemind.wardens.ticks.lease on a Swarm device's Warden (roadmap step 11.6).

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/lease.py (codingrules section 3); split by feature (14.2)
    from test_lease.py, which holds the Hive Stand's own paths. A device's Warden
    (`WardenDeps.device`) gives its lease back when it settles to WATCH with nothing to supervise,
    leases again on the next TaskAssign, tells the Queen once per lost link, and waits in WATCH
    (rather than ending its run loop) when a session cannot open. The Hive Stand's Warden does none
    of the first, which the same tests pin from the other side.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.lease for the module under test.
    - hivemind.wardens.deps.DeviceDeps for the switch the idle release reads.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from builders.quarantine import make_grant_issued, settle
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import (
    CellError,
    CellSession,
    LeaseReleaser,
    LeaseReleaseReport,
    LeaseRequest,
    RealCellLease,
)
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.source import CellIdentity
from hivemind.pheromone import TrailQuery
from hivemind.wardens import DeviceDeps, WardenDeps
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import FakeClock
from waggle.ids import WardenId
from waggle.messages import AlarmSeverity
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign, TaskOutcome, WorkerRole

_STOP_TIMEOUT_S = 5.0  # A hung stop fails the test instead of blocking the suite.


class _SwitchedReleaser:
    """Delegate to the fake releaser until the source says the Cell cannot confirm a release."""

    def __init__(self, inner: LeaseReleaser, source: _FlakySource) -> None:
        self._inner = inner
        self._source = source

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        if self._source.release_error is not None:
            raise self._source.release_error
        return await self._inner.release(lease)


class _FlakySource(FakeCellSource):
    """A fake source whose session can fail to open and whose releases can go unconfirmed."""

    def __init__(self, base: FakeCellSource, deps: WardenDeps) -> None:
        identity = CellIdentity(
            hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
        )
        # The same inventory the Warden's default source holds, so it leases the same Cell.
        super().__init__(base._cells.values(), identity, deps.trail, deps.clock)
        self.session_error: CellError | None = None
        self.release_error: CellError | None = None

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        lease = await super().lease(request)
        lease._releaser = _SwitchedReleaser(lease._releaser, self)
        return lease

    async def open_session(self, lease: RealCellLease) -> CellSession:
        if self.session_error is not None:
            raise self.session_error
        return await super().open_session(lease)


class _GatedReleaser:
    """Hold a release at the Cell until the test opens the gate: a release that takes a while."""

    def __init__(self, inner: LeaseReleaser) -> None:
        self._inner = inner
        self.entered = asyncio.Event()  # A release has reached the Cell.
        self.gate = asyncio.Event()  # Opened by the test: the Cell answers.

    async def release(self, lease: RealCellLease) -> LeaseReleaseReport:
        self.entered.set()
        await self.gate.wait()
        return await self._inner.release(lease)


class _GatedSource(FakeCellSource):
    """A fake source whose leases release only when the test lets them."""

    def __init__(self, base: FakeCellSource, deps: WardenDeps) -> None:
        identity = CellIdentity(
            hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
        )
        super().__init__(base._cells.values(), identity, deps.trail, deps.clock)
        self.releaser: _GatedReleaser | None = None

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        lease = await super().lease(request)
        self.releaser = _GatedReleaser(lease._releaser)
        lease._releaser = self.releaser
        return lease


def _writes_output(path: str = "output.txt") -> Callable[[WorkerRole], ScriptedWorker]:
    """A worker that writes the file the assignment's acceptance wants, then claims success."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        await asyncio.sleep(0)
        await ctx.session.put_file(Path(path), b"done")
        return make_outcome()

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


def _assignment(deps: WardenDeps) -> TaskAssign:
    """Build a task whose acceptance is the file `_writes_output` writes."""
    return make_assignment(
        clock=deps.clock,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="output.txt", argv=(), expected=None
            ),
        ),
    )


def _state_of(lease: RealCellLease) -> LeaseState:
    """Read `lease.state` through a call, so mypy does not carry an earlier narrowing over it."""
    return lease.state


async def _kinds(deps: WardenDeps) -> list[str]:
    """Every event kind on the Warden's trail, in the order it was recorded."""
    return [event.kind for event in await deps.trail.query(TrailQuery())]


async def _stop(warden: Warden, run_task: asyncio.Task[None]) -> None:
    """Stop `warden` and wait for its run loop to end, bounded."""
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=_STOP_TIMEOUT_S)


async def _started(deps: WardenDeps, warden_id: WardenId) -> tuple[Warden, asyncio.Task[None]]:
    """Start a Warden (it leases its Cell) and set its run loop going."""
    warden = Warden(warden_id, deps)
    await warden.start()
    return warden, asyncio.ensure_future(warden.run())


async def _wake(deps: WardenDeps, queen_end: QueenEnd) -> None:
    """Send the Warden one harmless message, so its tick runs and settles."""
    await queen_end.send(make_grant_issued(deps.clock, make_assignment(clock=deps.clock)))


async def test_a_device_warden_gives_its_lease_back_once_it_settles_to_watch() -> None:
    deps, queen_end, warden_id = make_warden_deps(device=DeviceDeps())
    warden, run_task = await _started(deps, warden_id)
    lease = warden.lease
    assert lease is not None  # Sanity: it leased at start and is ACTIVE until a tick settles it.

    await _wake(deps, queen_end)
    await settle(lambda: warden.lease is None)

    assert warden.state is WardenState.WATCH
    assert lease.state is LeaseState.RELEASED
    assert (await _kinds(deps)).count("cell.released") == 1
    await _stop(warden, run_task)


@pytest.mark.parametrize(
    "device", [None, DeviceDeps(release_lease_when_idle=False)], ids=["hive-stand", "switched-off"]
)
async def test_a_warden_without_the_idle_release_keeps_its_lease_in_watch(
    device: DeviceDeps | None,
) -> None:
    deps, queen_end, warden_id = make_warden_deps(device=device)
    warden, run_task = await _started(deps, warden_id)
    lease = warden.lease

    await _wake(deps, queen_end)
    await settle(lambda: warden.state is WardenState.WATCH)
    for _ in range(20):  # Let any release that was going to happen, happen.
        await asyncio.sleep(0)

    assert warden.lease is lease
    assert lease is not None and _state_of(lease) is LeaseState.OPEN
    assert "cell.released" not in await _kinds(deps)
    await _stop(warden, run_task)
    assert _state_of(lease) is LeaseState.RELEASED  # stop() still releases what it holds.


async def test_a_device_warden_leases_again_for_the_next_task_and_releases_when_it_ends() -> None:
    deps, queen_end, warden_id = make_warden_deps(
        device=DeviceDeps(), worker_factory=_writes_output()
    )
    assignment = _assignment(deps)
    warden, run_task = await _started(deps, warden_id)
    first = warden.lease
    await queen_end.send(make_grant_issued(deps.clock, assignment))
    await settle(lambda: warden.lease is None)  # Idle: the start-up lease is given back.

    await queen_end.send(assignment)  # No lease held: the Warden leases again, then spawns.
    result = await queen_end.wait_for_result()
    await settle(lambda: warden.lease is None)  # The task is over: given back once more.

    assert result.outcome is TaskOutcome.SUCCEEDED
    assert first is not None
    kinds = await _kinds(deps)
    assert kinds.count("cell.leased") == 2
    assert kinds.count("cell.released") == 2
    await _stop(warden, run_task)


async def test_a_device_warden_keeps_its_lease_while_an_assignment_waits_for_its_grant() -> None:
    deps, queen_end, warden_id = make_warden_deps(
        device=DeviceDeps(), worker_factory=_writes_output()
    )
    assignment = _assignment(deps)
    warden, run_task = await _started(deps, warden_id)
    lease = warden.lease

    await queen_end.send(assignment)  # Its grant has not arrived: parked, about to start.
    await settle(lambda: warden.state is WardenState.WATCH)
    for _ in range(20):
        await asyncio.sleep(0)
    assert warden.lease is lease  # Not released: that would only lease again a moment later.

    await queen_end.send(make_grant_issued(deps.clock, assignment))
    result = await queen_end.wait_for_result()

    assert result.outcome is TaskOutcome.SUCCEEDED
    await _stop(warden, run_task)


async def test_a_device_warden_holds_its_lease_while_its_link_is_lost_and_frees_it_after() -> None:
    deps, queen_end, warden_id = make_warden_deps(device=DeviceDeps())
    warden, run_task = await _started(deps, warden_id)
    lease = warden.lease
    await warden.cell_lost("the link closed")

    await _wake(deps, queen_end)
    await settle(lambda: warden.state is WardenState.WATCH)
    for _ in range(20):
        await asyncio.sleep(0)
    assert warden.lease is lease  # A release nothing could confirm would only orphan it.

    warden.cell_found()
    cast(FakeClock, deps.clock).advance(deps.heartbeat_interval_s + 0.1)  # Wakes the next tick.
    await settle(lambda: warden.lease is None)

    assert lease is not None and lease.state is LeaseState.RELEASED
    await _stop(warden, run_task)


@pytest.mark.parametrize("device", [None, DeviceDeps()], ids=["hive-stand", "device"])
async def test_a_lost_cell_link_raises_one_alarm_per_loss(device: DeviceDeps | None) -> None:
    deps, queen_end, warden_id = make_warden_deps(device=device)
    warden = Warden(warden_id, deps)
    await warden.start()

    await warden.cell_lost("the link closed")
    await warden.cell_lost("three heartbeats were missed")  # The same loss, reported twice.
    alarm = await queen_end.wait_for_alarm()

    assert alarm.kind is AlarmKind.CELL_UNREACHABLE
    assert alarm.severity is AlarmSeverity.WARNING
    assert alarm.origin == warden_id
    assert "the link closed" in alarm.detail
    assert (await _kinds(deps)).count("alarm.raised") == 1
    warden.cell_found()
    await warden.cell_lost("the link closed again")  # A new loss is its own Alarm.
    assert (await _kinds(deps)).count("alarm.raised") == 2
    await warden.stop()


async def test_a_session_that_cannot_open_leaves_the_warden_watching_not_crashed() -> None:
    deps, _queen_end, warden_id = make_warden_deps(device=DeviceDeps())
    source = _FlakySource(cast(FakeCellSource, deps.source), deps)
    source.session_error = CellError("the device went away")
    deps = dataclasses.replace(deps, source=source)
    warden = Warden(warden_id, deps)

    await warden.start()  # Must not raise.

    assert warden.state is WardenState.WATCH
    assert warden.lease is None  # The lease the device opened was given back.
    kinds = await _kinds(deps)
    assert kinds.count("cell.leased") == 1
    assert kinds.count("cell.released") == 1
    assert kinds[-1] == "warden.watch"
    await warden.stop()


async def test_a_release_the_cell_cannot_confirm_keeps_the_lease_for_a_later_try() -> None:
    deps, queen_end, warden_id = make_warden_deps(device=DeviceDeps())
    source = _FlakySource(cast(FakeCellSource, deps.source), deps)
    source.release_error = CellError("the device is away")
    deps = dataclasses.replace(deps, source=source)
    warden, run_task = await _started(deps, warden_id)
    lease = warden.lease
    assert lease is not None

    await _wake(deps, queen_end)
    await settle(lambda: lease.state is LeaseState.ORPHANED)
    assert warden.lease is lease  # Kept: forgetting it would leave the device leased for good.

    source.release_error = None  # The device answers again.
    cast(FakeClock, deps.clock).advance(deps.heartbeat_interval_s + 0.1)
    await settle(lambda: warden.lease is None)

    assert lease.state is LeaseState.RELEASED
    await _stop(warden, run_task)


async def test_a_stop_whose_lease_the_cell_cannot_release_still_finishes_and_says_so() -> None:
    deps, _queen_end, warden_id = make_warden_deps(device=DeviceDeps(release_lease_when_idle=False))
    source = _FlakySource(cast(FakeCellSource, deps.source), deps)
    source.release_error = CellError("the device is away")
    deps = dataclasses.replace(deps, source=source)
    warden, run_task = await _started(deps, warden_id)
    lease = warden.lease
    assert lease is not None

    await _stop(warden, run_task)  # Must not raise: the device is gone, the Warden is not.

    assert warden.state is WardenState.STOPPED
    assert lease.state is LeaseState.ORPHANED  # Left for the device's dead-man switch or a sweep.
    stopped = [e for e in await deps.trail.query(TrailQuery()) if e.kind == "warden.stopped"]
    assert [e.payload for e in stopped] == [{"lease_released": False, "release_error": "CellError"}]


async def _idle_release_in_flight(
    gated: _GatedSource, deps: WardenDeps, queen_end: QueenEnd, warden_id: WardenId
) -> tuple[Warden, asyncio.Task[None], _GatedReleaser]:
    """Start a device Warden and hold its idle release at the Cell, mid-flight."""
    warden, run_task = await _started(dataclasses.replace(deps, source=gated), warden_id)
    await _wake(deps, queen_end)  # The tick settles to WATCH and starts giving the lease back.
    releaser = gated.releaser
    assert releaser is not None
    await asyncio.wait_for(releaser.entered.wait(), timeout=_STOP_TIMEOUT_S)
    return warden, run_task, releaser


async def test_a_stop_racing_the_idle_release_waits_for_it_instead_of_raising() -> None:
    # The tick is mid-way through giving the lease back (RELEASING) when stop() reaches its own
    # release of the same lease: that used to raise InvalidLeaseTransitionError out of stop().
    deps, queen_end, warden_id = make_warden_deps(device=DeviceDeps())
    gated = _GatedSource(cast(FakeCellSource, deps.source), deps)
    warden, run_task, releaser = await _idle_release_in_flight(gated, deps, queen_end, warden_id)
    lease = warden.lease
    assert lease is not None

    stopping = asyncio.ensure_future(warden.stop())
    for _ in range(20):  # Let stop() get as far as it can: to the release already in flight.
        await asyncio.sleep(0)
    assert (
        not stopping.done()
    )  # It waits for the one release; it neither raises nor starts another.
    releaser.gate.set()
    await asyncio.wait_for(stopping, timeout=_STOP_TIMEOUT_S)
    await asyncio.wait_for(run_task, timeout=_STOP_TIMEOUT_S)

    assert warden.state is WardenState.STOPPED
    assert lease.state is LeaseState.RELEASED
    kinds = await _kinds(deps)
    assert kinds.count("cell.released") == 1  # Once, whichever of the two did the releasing.
    assert "warden.stopped" in kinds


async def test_a_stop_after_a_cancelled_idle_release_still_gives_the_lease_back() -> None:
    # A shutdown that cancels the run loop while it is giving the lease back leaves the lease
    # ORPHANED (never stuck RELEASING), so the stop that follows can release it.
    deps, queen_end, warden_id = make_warden_deps(device=DeviceDeps())
    gated = _GatedSource(cast(FakeCellSource, deps.source), deps)
    warden, run_task, releaser = await _idle_release_in_flight(gated, deps, queen_end, warden_id)
    lease = warden.lease
    assert lease is not None

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert _state_of(lease) is LeaseState.ORPHANED
    releaser.gate.set()  # The device answers the next release.
    await asyncio.wait_for(warden.stop(), timeout=_STOP_TIMEOUT_S)  # Must not raise.

    assert warden.state is WardenState.STOPPED
    assert _state_of(lease) is LeaseState.RELEASED
    assert (await _kinds(deps)).count("cell.released") == 1
