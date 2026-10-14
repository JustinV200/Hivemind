"""Tests for hivemind.wardens.offline.resume on a Level 0 device Warden whose Cell was lost.

Fits into the Hive:
    Mirrors src/hivemind/wardens/offline/resume.py (codingrules section 3), split by feature (14.2)
    from test_resume.py, which covers the link to the Queen. A device's Warden on the Hive Stand
    never goes OFFLINE; when the swarm gate says its device is gone (`Warden.cell_lost`) its bees
    are paused at a Handoff, and when it says the device is back (`Warden.cell_found`) the Warden
    gives the old lease back, leases the device again and starts the paused tasks from their
    Handoffs. A device that will not be leased yet is asked again after a pause.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.offline.resume for the module under test.
    - hivemind.wardens.ticks.lease for the lease the Warden gives back and takes again.
"""

from __future__ import annotations

import dataclasses

from builders.offline_warden import OfflineScene, pausable, start_offline_scene
from builders.quarantine import drain, settle

from hivemind.cell import Cell, CellSession, LeaseRefusedError, LeaseRequest, RealCellLease
from hivemind.cell.source import RealCellSource
from hivemind.memory import Handoff
from hivemind.pheromone import TrailQuery
from hivemind.wardens.offline import RESUME_RETRY_S
from hivemind.wardens.state import WardenState


class _RefusingSource:
    """A source that delegates to the real one, and can refuse a lease: the device is not ready."""

    def __init__(self, inner: RealCellSource) -> None:
        self._inner = inner
        self.refuse = False
        self.lease_calls = 0

    @property
    def name(self) -> str:
        return self._inner.name

    async def cells(self) -> tuple[Cell, ...]:
        return await self._inner.cells()

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        self.lease_calls += 1
        if self.refuse:
            raise LeaseRefusedError(request.cell_id, "the device is not ready")
        return await self._inner.lease(request)

    async def open_session(self, lease: RealCellLease) -> CellSession:
        return await self._inner.open_session(lease)


def _holds(scene: OfflineScene) -> bool:
    """Whether the outage holds a task, read through a call so mypy keeps no narrowing of it."""
    return scene.outage.has_held


async def _kinds(scene: OfflineScene) -> list[str]:
    """Every event kind on the Warden's trail, in the order it was recorded."""
    return [event.kind for event in await scene.deps.trail.query(TrailQuery())]


async def _lost_and_idle(scene: OfflineScene) -> None:
    """Lose the Cell, let the bee stop at its Handoff, and wait for the Warden to settle."""
    await scene.warden.cell_lost("the link closed")
    await scene.wait_paused()
    await settle(lambda: scene.warden.state is WardenState.WATCH)


async def test_a_found_cell_is_leased_again_and_the_paused_task_resumes_from_its_handoff() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed), remote=False)
    first_lease = scene.warden.lease
    await scene.warden.cell_lost("the link closed")
    bee = await scene.wait_paused()
    await settle(lambda: scene.warden.state is WardenState.WATCH)
    # Kept while the device is away: a release nothing could confirm would only orphan it.
    assert scene.warden.lease is first_lease
    before = await _kinds(scene)

    scene.warden.cell_found()
    await settle(lambda: bool(resumed))

    [successor] = scene.rows()
    assert successor.assignment.resume_from == bee.last_handoff
    assert scene.warden.lease is not None and scene.warden.lease is not first_lease
    after = await _kinds(scene)
    # The old lease went back and the device was leased once more; no Alarm beyond the loss's own.
    assert after.count("cell.released") == before.count("cell.released") + 1
    assert after.count("cell.leased") == before.count("cell.leased") + 1
    assert after.count("alarm.raised") == 1
    assert not _holds(scene)
    await scene.stop()


async def test_a_device_that_will_not_be_leased_yet_is_asked_again_after_a_pause() -> None:
    resumed: list[Handoff] = []
    scene = await start_offline_scene(pausable(resumed), remote=False)
    source = _RefusingSource(scene.warden._deps.source)
    scene.warden._deps = dataclasses.replace(scene.warden._deps, source=source)
    await _lost_and_idle(scene)
    source.refuse = True

    scene.warden.cell_found()
    await settle(lambda: source.lease_calls == 1)
    await settle(lambda: scene.warden.lease is None and scene.outage.retry_after is not None)

    assert resumed == [] and _holds(scene)  # Nothing resumes without a lease.
    source.refuse = False
    scene.outage.nudge()
    await drain()  # The pause has not run out: another tick asks nothing of the device.
    assert source.lease_calls == 1

    scene.clock.advance(RESUME_RETRY_S + 0.1)
    scene.outage.nudge()
    await settle(lambda: bool(resumed))
    assert source.lease_calls == 2 and not _holds(scene)
    await scene.stop()
