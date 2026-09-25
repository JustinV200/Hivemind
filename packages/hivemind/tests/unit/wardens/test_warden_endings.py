"""Tests for Warden: every way a sub-bee ends gives its slot back to the local pool.

A relayed `TaskCancel` used to leave the cancelled sub-bee in its Warden's table for good: the bee
reported KILLED on its next Heartbeat, the Warden mirrored the state and kept the row, and the
slot the row held never came back, so a Warden at its cap never started the next assignment.
Each test drives a real Warden over real `WorkerRuntime`s on a FakeClock, with a cap of one sub-bee,
and waits on state (what the Warden runs, what it has parked), never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/wardens/warden.py, ticks/heartbeat.py and ticks/control.py (codingrules
    section 3); split by feature (14.2) from test_warden_spawn_and_accept.py and
    test_warden_forwarding.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.alarms for retire_sub_bee, the one path every ending takes.
    - tests/unit/wardens/quarantine/test_path.py for the quarantine ending, which already took it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from builders.quarantine import make_grant_issued, settle
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment

from hivemind.supervision import Cancel
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.ids import TaskId
from waggle.messages.cell.status import ReleaseCause
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskAssign, TaskCancel, WorkerRole

_WORKER_BEAT_S = 1.0  # Each sub-bee's own cadence; the Warden's own never fires in these tests.
_HUNG_S = 1_000_000.0  # A role that never finishes on its own within any test here.


@dataclass(frozen=True, slots=True)
class _Scene:
    """A running Warden at a cap of one sub-bee, and the Queen's end of its link."""

    warden: Warden
    queen: QueenEnd
    clock: FakeClock
    run_task: asyncio.Task[None]


def _worker_factory(crashing: set[TaskId]) -> Callable[[WorkerRole], ScriptedWorker]:
    """Build roles that hang until cancelled, or crash at once for a task in `crashing`."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        if assignment.task_id in crashing:
            raise RuntimeError("The role crashed.")
        await ctx.clock.sleep(_HUNG_S)
        raise AssertionError("a hung role never wakes during these tests")

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


async def _start(first: TaskAssign, crashing: set[TaskId] | None = None) -> _Scene:
    """Start a Warden whose one-bee grant covers `first`, and wait until `first` runs."""
    clock = FakeClock()
    deps, queen, warden_id = make_warden_deps(
        clock,
        worker_factory=_worker_factory(crashing or set()),
        heartbeat_interval_s=_HUNG_S,
        worker_heartbeat_interval_s=_WORKER_BEAT_S,
    )
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    # One grant for every task in the test, shared (task_id=None), capped at one sub-bee.
    await queen.send(make_grant_issued(clock, first, task_id=None, max_sub_bees=1))
    await queen.send(first)
    await settle(lambda: _running(warden) == {first.task_id})
    return _Scene(warden=warden, queen=queen, clock=clock, run_task=run_task)


async def _park_second(scene: _Scene, first: TaskAssign) -> TaskAssign:
    """Send a second assignment under the same grant; it parks, the pool being full."""
    second = make_assignment(clock=scene.clock, grant_id=first.grant_id)
    await scene.queen.send(second)
    await settle(lambda: second.task_id in scene.warden._pending)
    return second


async def _stop(scene: _Scene) -> None:
    """Stop the Warden and let its run() return."""
    await scene.warden.stop()
    await asyncio.wait_for(scene.run_task, timeout=5.0)


def _running(warden: Warden) -> set[TaskId]:
    """The tasks the Warden has a sub-bee row for."""
    return {sub_bee.task_id for sub_bee in warden.sub_bees}


def _runtime_state(warden: Warden, task_id: TaskId) -> WorkerState | None:
    """The real state of `task_id`'s sub-bee runtime (not the Warden's mirror of it)."""
    rows = [sub_bee for sub_bee in warden.sub_bees if sub_bee.task_id == task_id]
    return rows[0].runtime.state if rows else None


async def _cancel_from_the_queen(scene: _Scene, task_id: TaskId) -> None:
    """The Queen withdraws the task: a TaskCancel the Warden relays to its sub-bee."""
    await scene.queen.send(TaskCancel(task_id=task_id, grace_s=0.0, reason="Withdrawn."))


async def _kill_by_the_warden(scene: _Scene, task_id: TaskId) -> None:
    """The Warden pulls its own Cancel lever on the sub-bee (`Supervisor.intervene`)."""
    [sub_bee] = [sb for sb in scene.warden.sub_bees if sb.task_id == task_id]
    await scene.warden.intervene(str(sub_bee.worker_id), Cancel(reason="Killed by its Warden."))


@pytest.mark.parametrize("order", [_cancel_from_the_queen, _kill_by_the_warden])
async def test_a_cancelled_sub_bee_frees_its_slot_for_the_next_assignment(
    order: Callable[[_Scene, TaskId], Awaitable[None]],
) -> None:
    # Arrange: the first task runs in the only slot, the second is parked for it.
    first = make_assignment()
    scene = await _start(first)
    second = await _park_second(scene, first)

    await order(scene, first.task_id)
    await settle(lambda: _runtime_state(scene.warden, first.task_id) is WorkerState.KILLED)
    # The bee's next Heartbeat reports KILLED: the one thing that tells its Warden it has ended.
    scene.clock.advance(_WORKER_BEAT_S)
    await settle(lambda: _running(scene.warden) == {second.task_id})

    assert scene.warden._sub_bee_slots.in_use == 1
    assert second.task_id not in scene.warden._pending
    await _stop(scene)


async def test_a_cancel_for_a_sub_bee_that_already_failed_frees_its_slot() -> None:
    # Arrange: a third attempt crashes; the policy escalates, so the FAILED row waits on the Queen.
    first = make_assignment(attempt=3)
    scene = await _start(first, crashing={first.task_id})
    alarm = await scene.queen.wait_for_alarm()
    assert alarm.kind.value == "WORKER_CRASHED"
    second = await _park_second(scene, first)
    scene.clock.advance(_WORKER_BEAT_S)
    await settle(lambda: _mirrored(scene.warden, first.task_id) is WorkerState.FAILED)

    await _cancel_from_the_queen(scene, first.task_id)
    await settle(lambda: _running(scene.warden) == {second.task_id})

    assert scene.warden._sub_bee_slots.in_use == 1
    await _stop(scene)


async def test_a_cancel_racing_the_failed_heartbeat_still_frees_the_slot() -> None:
    # The Queen's cancel lands before the Warden has mirrored FAILED: it is relayed, and the
    # FAILED Heartbeat that follows ends the row.
    first = make_assignment(attempt=3)
    scene = await _start(first, crashing={first.task_id})
    await scene.queen.wait_for_alarm()
    second = await _park_second(scene, first)

    await _cancel_from_the_queen(scene, first.task_id)
    await settle(lambda: _row(scene.warden, first.task_id).cancelled)
    assert _mirrored(scene.warden, first.task_id) is WorkerState.SPAWNED  # No Heartbeat yet.
    scene.clock.advance(_WORKER_BEAT_S)
    await settle(lambda: _running(scene.warden) == {second.task_id})

    assert scene.warden._sub_bee_slots.in_use == 1
    await _stop(scene)


async def test_releasing_the_lease_frees_every_slot() -> None:
    first = make_assignment()
    scene = await _start(first)
    release = Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,
        task_id=None,
        slot=None,
        binding=None,
        alarm_id=None,
        reason="The operator took the Cell back.",
    )

    await scene.queen.send(release)
    released = await scene.queen.wait_for_lease_released()

    assert released.cause is ReleaseCause.COMPLETED
    assert _running(scene.warden) == set()
    assert scene.warden._sub_bee_slots.in_use == 0
    await _stop(scene)


async def test_stopping_the_warden_frees_every_slot() -> None:
    first = make_assignment()
    scene = await _start(first)

    await _stop(scene)

    assert _running(scene.warden) == set()
    assert scene.warden._sub_bee_slots.in_use == 0


def _mirrored(warden: Warden, task_id: TaskId) -> WorkerState | None:
    """The Warden's own mirror of `task_id`'s sub-bee state, from its last Heartbeat."""
    rows = [sub_bee for sub_bee in warden.sub_bees if sub_bee.task_id == task_id]
    return rows[0].state if rows else None


def _row(warden: Warden, task_id: TaskId) -> SubBee:
    """The Warden's own row for `task_id`'s sub-bee; the test fails if there is none."""
    [row] = [sub_bee for sub_bee in warden.sub_bees if sub_bee.task_id == task_id]
    return row
