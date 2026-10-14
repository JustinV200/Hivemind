"""Tests for hivemind.wardens.ticks.assign on a lease the device refuses (roadmap step 11.8a).

A Propolis device refuses to be leased until its operator's tunnel verifies, and a device can be
away. A task that arrives meanwhile used to draw one Alarm and be dropped, stranding it RUNNING in
the Brood Chamber with nobody to run it, so the exit criterion "a Propolis task is blocked until the
tunnel is verified" held only for a task submitted after the tunnel was up. A device's Warden now
keeps the assignment, tells the Queen once, asks for the lease again every `lease_retry_s` and
starts the task when it is granted; the Queen's cancel ends the wait; and the Hive Stand's Warden,
which has no device, keeps dropping the task after its one Alarm.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/assign.py (codingrules section 3); split by feature (14.2)
    from the other assign tests.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.deps.DeviceDeps for `lease_retry_s`.
    - tests/e2e/test_phase11_exit_propolis.py for the same wait against a real device.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import cast

from builders.quarantine import make_grant_issued, settle
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import LeaseRefusedError, LeaseRequest, RealCellLease
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.source import CellIdentity
from hivemind.wardens import DeviceDeps, WardenDeps
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import FakeClock
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign, TaskCancel, TaskOutcome, WorkerRole

_RETRY_S = 5.0  # The device Warden's wait between two requests for the lease.
_STOP_TIMEOUT_S = 5.0  # A hung stop fails the test instead of blocking the suite.
_WAIT_S = 10.0  # Real seconds a task that should start is given to report; it takes microseconds.


class _RefusingSource(FakeCellSource):
    """A fake source that refuses every lease while the test says so, and counts the requests."""

    def __init__(self, base: FakeCellSource, deps: WardenDeps) -> None:
        identity = CellIdentity(
            hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
        )
        # The same inventory the Warden's default source holds, so it leases the same Cell.
        super().__init__(base._cells.values(), identity, deps.trail, deps.clock)
        self.refusing = True
        self.asked = 0

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        self.asked += 1
        if self.refusing:
            raise LeaseRefusedError(request.cell_id, "its tunnel is not verified")
        return await super().lease(request)


def _writes_output() -> Callable[[WorkerRole], ScriptedWorker]:
    """A worker that writes the file the assignment's acceptance wants, then claims success."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        await asyncio.sleep(0)
        await ctx.session.put_file(Path("output.txt"), b"done")
        return make_outcome()

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


class _Scene:
    """A Warden whose device refuses its lease, a task waiting for it, and the Queen's end."""

    def __init__(self, deps: WardenDeps, queen_end: QueenEnd, warden: Warden) -> None:
        self.deps, self.queen_end, self.warden = deps, queen_end, warden
        self.source = cast(_RefusingSource, deps.source)
        self.assignment = make_assignment(
            clock=deps.clock,
            acceptance=(
                Postcondition(
                    kind=PostconditionKind.FILE_EXISTS,
                    subject="output.txt",
                    argv=(),
                    expected=None,
                ),
            ),
        )
        self.run_task: asyncio.Task[None] | None = None

    def advance(self, seconds: float) -> None:
        """Move the Warden's clock on, which wakes its tick."""
        cast(FakeClock, self.deps.clock).advance(seconds)

    async def stop(self) -> None:
        """Stop the Warden and wait for its run loop to end, bounded."""
        await self.warden.stop()
        if self.run_task is not None:
            await asyncio.wait_for(self.run_task, timeout=_STOP_TIMEOUT_S)


async def _scene(device: DeviceDeps | None) -> _Scene:
    """Start a Warden whose lease was refused, and hand it a task the Queen has granted."""
    deps, queen_end, warden_id = make_warden_deps(device=device, worker_factory=_writes_output())
    deps = dataclasses.replace(
        deps, source=_RefusingSource(cast(FakeCellSource, deps.source), deps)
    )
    warden = Warden(warden_id, deps)
    await warden.start()  # Refused: the Warden is in WATCH, holding no lease.
    scene = _Scene(deps, queen_end, warden)
    scene.run_task = asyncio.ensure_future(warden.run())
    await queen_end.send(make_grant_issued(deps.clock, scene.assignment))
    await queen_end.send(scene.assignment)
    return scene


async def _asks(scene: _Scene, requests: int) -> None:
    """Let the Warden's ticks run until the device has been asked for `requests` leases."""
    await settle(lambda: scene.source.asked >= requests, rounds=500)


async def test_a_device_warden_keeps_a_refused_task_and_runs_it_once_the_device_allows_it() -> None:
    scene = await _scene(DeviceDeps(lease_retry_s=_RETRY_S))
    alarm = await scene.queen_end.wait_for_alarm()
    assert alarm.kind is AlarmKind.CELL_UNREACHABLE
    assert alarm.context.task_id == scene.assignment.task_id
    assert scene.source.asked == 2  # At start, and once more when the task arrived.

    scene.source.refusing = False  # The operator's tunnel is up.
    scene.advance(_RETRY_S + 0.1)
    async with asyncio.timeout(_WAIT_S):  # A task the Warden dropped is a failure, not a hang.
        result = await scene.queen_end.wait_for_result()

    assert result.outcome is TaskOutcome.SUCCEEDED
    assert result.task_id == scene.assignment.task_id
    assert len(scene.queen_end.alarms) == 1  # The Queen heard of the refusal once.
    await scene.stop()


async def test_a_device_warden_asks_for_the_lease_once_per_interval_and_alarms_once() -> None:
    scene = await _scene(DeviceDeps(lease_retry_s=_RETRY_S))
    await scene.queen_end.wait_for_alarm()
    asked = scene.source.asked

    scene.advance(_RETRY_S / 2)  # Too soon: it stays parked and does not ask.
    for _ in range(50):
        await asyncio.sleep(0)
    assert scene.source.asked == asked

    for round_ in range(1, 4):  # Each interval is one request, however many ticks it holds.
        scene.advance(_RETRY_S)
        await _asks(scene, asked + round_)
        for _ in range(50):
            await asyncio.sleep(0)
        assert scene.source.asked == asked + round_
    await scene.queen_end.pump_until(lambda: True)
    assert len(scene.queen_end.alarms) == 1
    await scene.stop()


async def test_the_queens_cancel_ends_the_wait_for_a_lease() -> None:
    scene = await _scene(DeviceDeps(lease_retry_s=_RETRY_S))
    await scene.queen_end.wait_for_alarm()
    asked = scene.source.asked

    cancel = TaskCancel(task_id=scene.assignment.task_id, grace_s=0.0, reason="No longer wanted.")
    await scene.queen_end.send(cancel)
    scene.advance(_RETRY_S + 0.1)  # Wakes a tick, which hears the cancel.
    for _ in range(100):
        await asyncio.sleep(0)
    scene.source.refusing = False
    scene.advance(_RETRY_S + 0.1)  # Time to ask again, if there were anything to ask for.
    for _ in range(100):
        await asyncio.sleep(0)

    assert scene.source.asked == asked  # Nothing was kept, so nothing was asked.
    assert scene.warden.lease is None and scene.warden.sub_bees == ()
    await scene.stop()


async def test_a_hive_stands_warden_still_drops_the_task_after_one_alarm() -> None:
    scene = await _scene(None)
    alarm = await scene.queen_end.wait_for_alarm()
    assert alarm.kind is AlarmKind.CELL_UNREACHABLE
    asked = scene.source.asked

    scene.source.refusing = False
    scene.advance(_RETRY_S * 10)
    for _ in range(100):
        await asyncio.sleep(0)

    assert scene.source.asked == asked  # It never asked again: there is no device to wait for.
    assert scene.warden.sub_bees == ()
    await scene.stop()
