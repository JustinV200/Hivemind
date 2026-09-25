"""Tests for Warden: a task's next bee replaces the one before it, never orphaned or doubled.

Two ways one task's bees used to go wrong on a Warden:

- The Queen's retry of an escalated crash reached the Warden as a fresh TaskAssign while the
  crashed bee's FAILED row still held the task's slot: at a cap of one the retry parked for good,
  above it the new bee ran beside the FAILED row.
- A Warden-local respawn gave the task's slot back and started the next bee without taking it
  again, so the pool counted one bee too few and started a parked assignment beside it.

Each test drives a real Warden over real `WorkerRuntime`s on a FakeClock and waits on state (what
the Warden runs, what it has parked, what the bees report), never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/alarms.py and ticks/assign.py
    (codingrules section 3); split by feature (14.2) from test_warden_endings.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.alarms for the respawn that keeps its slot.
    - hivemind.wardens.ticks.assign for handle_assign, where a new attempt supersedes the old.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from builders.quarantine import make_grant_issued, settle
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import RunScript, ScriptedWorker, make_assignment

from hivemind.memory import Handoff
from hivemind.wardens.spawn.sub_bee import SubBee
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.ids import TaskId, new_grant_id
from waggle.messages.task import TaskAssign

_WORKER_BEAT_S = 1.0  # Each sub-bee's own cadence.
_WARDEN_BEAT_S = 10.0  # The Warden's own; it never fires in these tests.
_HUNG_S = 1_000_000.0  # A role that never finishes on its own within any test here.


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
