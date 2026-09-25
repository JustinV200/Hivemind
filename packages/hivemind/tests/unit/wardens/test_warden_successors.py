"""Tests for Warden: a task's next bee replaces the one before it, never orphaned or doubled.

Three ways one task's bees used to go wrong on a Warden:

- A sub-bee its Warden ordered to hand off for its context size wrote its Handoff and stopped
  (DONE), and nothing started the fresh bee meant to resume from it: the task stayed RUNNING with
  no bee at all.
- The Queen's retry of an escalated crash reached the Warden as a fresh TaskAssign while the
  crashed bee's FAILED row still held the task's slot: at a cap of one the retry parked for good,
  above it the new bee ran beside the FAILED row.
- A Warden-local respawn gave the task's slot back and started the next bee without taking it
  again, so the pool counted one bee too few and started a parked assignment beside it.

Each test drives a real Warden over real `WorkerRuntime`s on a FakeClock and waits on state (what
the Warden runs, what it has parked, what the bees report), never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/heartbeat.py, ticks/alarms.py and ticks/assign.py
    (codingrules section 3); split by feature (14.2) from test_warden_endings.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.alarms for resume_from_handoff and the respawn that keeps its slot.
    - hivemind.wardens.ticks.assign for handle_assign, where a new attempt supersedes the old.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import pytest
from builders.memory import make_handoff
from builders.quarantine import make_grant_issued, settle
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import RunScript, ScriptedWorker, make_assignment, make_outcome

from hivemind.memory import Handoff
from hivemind.supervision import Handoff as HandoffLever
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.ids import TaskId, new_grant_id
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskAssign, TaskPause

_WORKER_BEAT_S = 1.0  # Each sub-bee's own cadence.
_WARDEN_BEAT_S = 10.0  # The Warden's own: its context check rides this one.
_HUNG_S = 1_000_000.0  # A role that never finishes on its own within any test here.
_WINDOW = 1_000  # The context window every scripted bee reports against.
_FULL = 950  # Tokens used: past the 0.66 handoff threshold the test deps carry.


@dataclass(frozen=True, slots=True)
class _Scene:
    """A running Warden and the Queen's end of its link."""

    warden: Warden
    queen: QueenEnd
    clock: FakeClock
    run_task: asyncio.Task[None]


async def _start(script: RunScript, first: TaskAssign, *, cap: int = 1) -> _Scene:
    """Start a Warden whose `cap`-bee grant covers `first`, and wait until `first` runs."""
    clock = FakeClock()
    deps, queen, warden_id = make_warden_deps(
        clock,
        worker_factory=lambda role: ScriptedWorker(script, role=role),
        heartbeat_interval_s=_WARDEN_BEAT_S,
        worker_heartbeat_interval_s=_WORKER_BEAT_S,
    )
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    # One grant shared by every task of the test (task_id=None), so a second one parks behind it.
    await queen.send(make_grant_issued(clock, first, task_id=None, max_sub_bees=cap))
    await queen.send(first)
    await settle(lambda: _rows(warden, first.task_id) != [])
    return _Scene(warden=warden, queen=queen, clock=clock, run_task=run_task)


async def _stop(scene: _Scene) -> None:
    """Stop the Warden and let its run() return."""
    await scene.warden.stop()
    await asyncio.wait_for(scene.run_task, timeout=5.0)


def _rows(warden: Warden, task_id: TaskId) -> list[SubBee]:
    """Every sub-bee row the Warden holds for `task_id`."""
    return [sub_bee for sub_bee in warden.sub_bees if sub_bee.task_id == task_id]


async def _running_bee(scene: _Scene, task_id: TaskId) -> SubBee:
    """Wait until `task_id`'s one bee runs its role (its heartbeat is armed from then on)."""
    [bee] = _rows(scene.warden, task_id)
    await settle(lambda: bee.runtime.state is WorkerState.RUNNING)
    return bee


def _handoff_then_resume(resumed: list[Handoff]) -> RunScript:
    """A role that fills its context and hands off when told; resumed, it records what from."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        if resume_from is not None:
            resumed.append(resume_from)
            await ctx.clock.sleep(_HUNG_S)  # The fresh bee carries on with the work.
        ctx.telemetry.record_tokens(_FULL, _WINDOW)
        # ASYNC110: the runtime sets a plain bool flag on the lever; no Event exists to wait on.
        while not ctx.telemetry.handoff_requested:  # noqa: ASYNC110
            await asyncio.sleep(0)
        return make_outcome(claimed=False, handoff=make_handoff(written_by="first bee"))

    return script


def _crash_on(attempt: int) -> RunScript:
    """A role that crashes at once on `attempt`, and otherwise works until stopped."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        if assignment.attempt == attempt:
            raise RuntimeError("The role crashed.")
        await ctx.clock.sleep(_HUNG_S)
        raise AssertionError("a working role never wakes during these tests")

    return script


async def _order_by_context(scene: _Scene, bee: SubBee) -> None:
    """The bee reports a full context; the Warden's own next Heartbeat orders the handoff."""
    scene.clock.advance(_WORKER_BEAT_S)
    await settle(lambda: bee.last_telemetry is not None and bee.last_telemetry.tokens_used == _FULL)
    scene.clock.advance(_WARDEN_BEAT_S - _WORKER_BEAT_S)


async def _order_by_lever(scene: _Scene, bee: SubBee) -> None:
    """The Warden pulls its own Handoff lever on the bee (`Supervisor.intervene`)."""
    await scene.warden.intervene(str(bee.worker_id), HandoffLever(reason="Reset for a fresh bee."))


@pytest.mark.parametrize("order", [_order_by_context, _order_by_lever])
async def test_a_handoff_its_warden_orders_is_resumed_by_a_fresh_bee(
    order: Callable[[_Scene, SubBee], Awaitable[None]],
) -> None:
    resumed: list[Handoff] = []
    first = make_assignment()
    scene = await _start(_handoff_then_resume(resumed), first)
    bee = await _running_bee(scene, first.task_id)

    await order(scene, bee)
    await settle(lambda: bee.runtime.state is WorkerState.DONE and bee.last_handoff is not None)
    checkpoint = bee.last_handoff
    scene.clock.advance(_WORKER_BEAT_S)  # Its DONE Heartbeat: the one report that it has ended.
    await settle(lambda: bool(resumed))

    [successor] = _rows(scene.warden, first.task_id)
    assert successor.worker_id != bee.worker_id
    assert successor.attempt == first.attempt  # The same attempt carried on, not a retry.
    assert successor.assignment.resume_from == checkpoint
    assert resumed[0].written_by == "first bee"  # It resumed from the Handoff the first wrote.
    assert scene.warden._sub_bee_slots.in_use == 1
    await _stop(scene)


async def test_a_handoff_the_queen_orders_waits_for_her_resume() -> None:
    # Clustering and isolation pause a task with Intervene(HANDOFF) then TaskPause and resume it
    # themselves with a fresh TaskAssign: the Warden starts no bee of its own meanwhile, and stays
    # CLUSTERED once the paused bee's row is gone (it used to ask for CLUSTERED -> WATCH, no edge).
    resumed: list[Handoff] = []
    first = make_assignment()
    scene = await _start(_handoff_then_resume(resumed), first)
    bee = await _running_bee(scene, first.task_id)
    await scene.queen.send(_queen_handoff(first.task_id))
    await scene.queen.send(TaskPause(task_id=first.task_id, reason="Clustering."))
    await settle(lambda: bee.runtime.state is WorkerState.DONE and bee.last_handoff is not None)
    scene.clock.advance(_WORKER_BEAT_S)
    await settle(lambda: _rows(scene.warden, first.task_id) == [])
    assert (resumed, scene.warden.state) == ([], WardenState.CLUSTERED)
    assert scene.warden._sub_bee_slots.in_use == 0
    resume = first.model_copy(
        update={
            "attempt": 2,
            "grant_id": new_grant_id(scene.clock),
            "resume_from": bee.last_handoff,
        }
    )

    await scene.queen.send(make_grant_issued(scene.clock, resume))
    await scene.queen.send(resume)
    await settle(lambda: bool(resumed) and scene.warden.state is WardenState.ACTIVE)

    assert [row.attempt for row in _rows(scene.warden, first.task_id)] == [2]
    await _stop(scene)


def _queen_handoff(task_id: TaskId) -> Intervene:
    """The Queen's Intervene(HANDOFF) for `task_id`, as Clustering and isolation send it."""
    return Intervene(
        action=InterventionAction.HANDOFF,
        subject=None,
        task_id=task_id,
        slot=None,
        binding=None,
        alarm_id=None,
        reason="Clustering: provider unavailable.",
    )


@pytest.mark.parametrize("cap", [1, 2])
async def test_a_queen_retry_replaces_the_escalated_failed_row(cap: int) -> None:
    # A third attempt crashes; the Warden's policy escalates, leaving the FAILED row to the Queen.
    first = make_assignment(attempt=3)
    scene = await _start(_crash_on(3), first, cap=cap)
    await scene.queen.wait_for_alarm()
    scene.clock.advance(_WORKER_BEAT_S)
    await settle(lambda: [row.state for row in _rows(scene.warden, first.task_id)] == [_FAILED])
    # The Queen retries: a fresh grant, then the task's next attempt under it.
    retry = first.model_copy(update={"attempt": 4, "grant_id": new_grant_id(scene.clock)})

    await scene.queen.send(make_grant_issued(scene.clock, retry, max_sub_bees=cap))
    await scene.queen.send(retry)
    await settle(lambda: [row.attempt for row in _rows(scene.warden, first.task_id)] == [4])

    [row] = _rows(scene.warden, first.task_id)
    assert row.state is not WorkerState.FAILED
    assert first.task_id not in scene.warden._pending
    assert scene.warden._sub_bee_slots.in_use == 1
    await _stop(scene)


async def test_a_local_respawn_keeps_the_tasks_slot() -> None:
    # The first attempt crashes; the Warden's own policy respawns it as attempt 2, in its slot.
    first = make_assignment()
    scene = await _start(_crash_on(1), first)
    await settle(lambda: [row.attempt for row in _rows(scene.warden, first.task_id)] == [2])
    second = make_assignment(clock=scene.clock, grant_id=first.grant_id)

    await scene.queen.send(second)
    await settle(lambda: second.task_id in scene.warden._pending)

    assert {row.task_id for row in scene.warden.sub_bees} == {first.task_id}
    assert scene.warden._sub_bee_slots.in_use == 1
    await _stop(scene)


_FAILED = WorkerState.FAILED
