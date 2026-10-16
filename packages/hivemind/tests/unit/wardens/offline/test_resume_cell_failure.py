"""Tests for a sub-bee that fails because its Level 0 device's Cell link went (roadmap 11.6, 11.9).

Fits into the Hive:
    Mirrors `wait_out_the_cell` in src/hivemind/wardens/ticks/lease.py (codingrules section 3),
    split by feature (14.2) from test_resume_cell.py, which covers the pause and the resume of a
    bee that obeyed. A bee blocked in a command when the link dies cannot hear its Warden's order
    to stop at a Handoff; the command returns by failing, and that failure is the cut's doing and
    not the bee's. The Warden holds the task for the outage instead of counting a crash: no retry,
    no rebind, no escalation, no second bee on a session that is gone, and the task starts again
    from the Handoff it had when the device is back. Found by the phase 11 exit scenario on a real
    container, which left the task RUNNING with a crashed bee and nothing to run it.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.lease for the module under test.
    - tests/e2e/test_phase11_exit_deadman.py for the same thing across a real cable.
"""

from __future__ import annotations

import asyncio

from builders.offline_warden import OfflineScene, pausable, start_offline_scene
from builders.quarantine import drain, settle
from builders.wardens import make_warden_deps
from builders.workers import RunScript

from hivemind.cell import CellError
from hivemind.memory import Handoff
from hivemind.pheromone import TrailQuery
from hivemind.wardens.offline import RESUME_SETTLE_S
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.ticks.lease import HELD_FOR_CELL, wait_out_the_cell
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.ids import new_alarm_id, new_event_id
from waggle.messages import AlarmSeverity, HandoffRef
from waggle.messages.labels import HoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import TaskAssign

_HUNG_S = 1_000_000.0  # A bee that works on, and never finishes by itself within a test.


def _dies_with_the_link(fail: asyncio.Event, started: list[Handoff | None]) -> RunScript:
    """A role blocked in a command until the test fails it with a device error; restarted, it works.

    Args:
        fail: Set by the test when the command should die, as it does when the link ends.
        started: Appended with the Handoff each start of the role resumes from.

    Returns:
        The script for a `ScriptedWorker`.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        started.append(resume_from)
        if len(started) > 1:
            await ctx.clock.sleep(_HUNG_S)  # A bee started after the first one works on.
        await fail.wait()  # The command, which no order to hand off can interrupt ...
        raise CellError("the link to the device ended before the command was answered")

    return script


def _failure(scene: OfflineScene, bee: SubBee, kind: AlarmKind) -> AlarmRaised:
    """The Alarm `bee` raises for a failure of `kind`, as its runtime sends it to the Warden."""
    return AlarmRaised(
        alarm_id=new_alarm_id(scene.clock),
        kind=kind,
        severity=AlarmSeverity.WARNING,
        origin=bee.worker_id,
        attempts=0,
        raised_at=scene.clock.now(),
        context=AlarmContext(
            task_id=bee.task_id, cell_id=None, worker_id=bee.worker_id, event_id=None, handoff=None
        ),
        detail="A command died under the bee.",
        clearance=HoneyClearance.C1,
        reason="The role raised while running.",
    )


async def _events(scene: OfflineScene, kind: str) -> list[dict[str, object]]:
    """The payload of every event of `kind` on the Warden's trail, oldest first."""
    return [dict(event.payload) for event in await scene.deps.trail.query(TrailQuery(kind=kind))]


async def test_a_bee_that_dies_with_the_link_is_retired_and_its_task_waits_for_the_device() -> None:
    fail, started = asyncio.Event(), list[Handoff | None]()
    scene = await start_offline_scene(_dies_with_the_link(fail, started), remote=False)
    task_id = scene.assignment.task_id
    await scene.warden.cell_lost("the link closed")
    await settle(lambda: scene.outage.is_holding(task_id))  # Held at the cut, as every task is.

    fail.set()  # The command dies with the link.
    await settle(lambda: scene.rows() == [])  # The failed bee is only retired ...

    assert len(started) == 1  # ... nothing was started on a session that is gone,
    assert scene.outage.is_holding(task_id)  # the task waits for the device,
    [handled] = await _events(scene, "alarm.handled")
    assert handled["action"] == HELD_FOR_CELL  # it says why it did nothing about the crash,
    assert await _events(scene, "alarm.escalated") == []  # and nobody is told it crashed.
    await scene.stop()


async def test_the_held_task_starts_again_from_where_it_was_when_the_device_is_back() -> None:
    fail, started = asyncio.Event(), list[Handoff | None]()
    scene = await start_offline_scene(_dies_with_the_link(fail, started), remote=False)
    await scene.warden.cell_lost("the link closed")
    await settle(lambda: scene.outage.is_holding(scene.assignment.task_id))
    fail.set()
    await settle(lambda: scene.rows() == [])

    scene.warden.cell_found()
    await drain()  # The tick notes the device is back, and gives it a moment to report first.
    scene.clock.advance(RESUME_SETTLE_S + 0.1)
    scene.outage.nudge()
    await settle(lambda: len(started) == 2)

    assert started[1] is None  # Nothing had been checkpointed: the same task, started again.
    [bee] = scene.rows()
    assert bee.assignment.attempt == scene.assignment.attempt  # Not a retry: it never failed.
    assert not scene.outage.has_held
    await scene.stop()


async def test_a_failure_the_pause_has_not_reached_yet_is_held_all_the_same() -> None:
    scene = await start_offline_scene(pausable([]), remote=False)
    [bee] = scene.rows()
    scene.warden._cell_lost = True  # The gate has said so; no tick has paused anything yet.

    failure = _failure(scene, bee, AlarmKind.WORKER_FAILED)
    assert await wait_out_the_cell(scene.warden, bee, failure)

    assert scene.rows() == []
    assert scene.outage.is_holding(scene.assignment.task_id)
    await scene.stop()


async def test_a_failure_with_the_cell_there_is_the_bees_own_and_is_left_to_the_policy() -> None:
    scene = await start_offline_scene(pausable([]), remote=False)
    [bee] = scene.rows()

    failure = _failure(scene, bee, AlarmKind.WORKER_CRASHED)
    assert not await wait_out_the_cell(scene.warden, bee, failure)

    assert scene.rows() == [bee]
    assert not scene.outage.has_held
    await scene.stop()


async def test_a_failure_of_another_kind_is_left_to_the_policy_even_with_the_cell_lost() -> None:
    scene = await start_offline_scene(pausable([]), remote=False)
    [bee] = scene.rows()
    scene.warden._cell_lost = True

    failure = _failure(scene, bee, AlarmKind.GRANT_EXCEEDED)
    assert not await wait_out_the_cell(scene.warden, bee, failure)

    assert scene.rows() == [bee]  # A bee that overspent did not overspend because of a cut.
    await scene.stop()


async def test_a_warden_without_an_outage_leaves_every_failure_to_the_policy() -> None:
    scene = await start_offline_scene(pausable([]), remote=False)
    [bee] = scene.rows()
    deps, _queen, warden_id = make_warden_deps(scene.clock)  # The Hive Stand's kind: no device.
    bare = Warden(warden_id, deps)
    bare._cell_lost = True

    failure = _failure(scene, bee, AlarmKind.WORKER_CRASHED)
    assert not await wait_out_the_cell(bare, bee, failure)

    await scene.stop()


async def test_a_handoff_written_after_the_pause_took_the_task_is_the_one_it_resumes_from() -> None:
    scene = await start_offline_scene(pausable([]), remote=False)
    [bee] = scene.rows()
    scene.warden._cell_lost = True
    scene.outage.hold(
        bee.assignment, None, scene.clock.now()
    )  # Held at the cut, as `cut_cell` does.
    # Inside the command that then dies, the bee checkpoints: its runtime reports the Handoff.
    bee.last_handoff = HandoffRef(
        event_id=new_event_id(scene.clock),
        written_at=scene.clock.now(),
        clearance=HoneyClearance.C1,
    )

    assert await wait_out_the_cell(scene.warden, bee, _failure(scene, bee, AlarmKind.WORKER_FAILED))

    [held] = scene.outage.held()
    assert held.handoff == bee.last_handoff  # Not the older one the hold was taken with.
    await scene.stop()
