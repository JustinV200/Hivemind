"""Tests for Warden: a bee whose runtime died does not end the Warden that retires it.

The Cell's Warden used to exit when the Queen resumed a paused task: its sub-bee's runtime had died
of an error it did not handle, the error sat in the task the Warden owns, and the Warden met it
when the resume's fresh assignment superseded the bee (`ticks.assign._supersede`), and again when
it was stopped (`Warden.stop`). Each scenario drives a real Warden over real `WorkerRuntime`s on a
FakeClock, kills one bee's runtime with a trail that cannot record `worker.started`, and asserts
the Warden carries on: the supersede spawns the new attempt, and the stop completes.

Fits into the Hive:
    Mirrors src/hivemind/wardens/warden.py and ticks/assign.py (codingrules section 3); split by
    feature (14.2) from test_warden_endings.py, whose scene this one borrows the shape of.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.spawn.stop_sub_bee for the one place a runtime's task is reaped.
    - tests.unit.wardens.spawn.test_spawn_stop for the same without a whole Warden.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from builders.quarantine import make_grant_issued, settle
from builders.trail_faults import FailingTrail
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment
from structlog.testing import capture_logs

from hivemind.pheromone import TrailQuery
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.state import WorkerState
from waggle.clock import FakeClock
from waggle.messages.task import TaskAssign, WorkerRole

_WORKER_BEAT_S = 1.0  # Each sub-bee's own cadence; the Warden's own never fires in these tests.
_HUNG_S = 1_000_000.0  # A role that never finishes on its own within any test here.
_WAIT_S = 5.0  # How long a scenario waits for the Warden to finish a step before it fails.


@dataclass(frozen=True, slots=True)
class _Scene:
    """A running Warden whose first bee's runtime has died, and the Queen's end of its link."""

    warden: Warden
    queen: QueenEnd
    clock: FakeClock
    run_task: asyncio.Task[None]
    first: TaskAssign


def _hanging_worker(role: WorkerRole) -> ScriptedWorker:
    """Build a role that waits for ever, so only its runtime's own death ends a bee."""

    async def script(ctx: WorkerContext, _assign: TaskAssign, _resume: object) -> WorkerOutcome:
        await ctx.clock.sleep(_HUNG_S)
        raise AssertionError("a hung role never wakes during these tests")

    return ScriptedWorker(script, role=role)


async def _start_with_a_dead_bee() -> _Scene:
    """Start a Warden, give it a task, and wait until that task's runtime has died."""
    clock = FakeClock()
    # The disk fills once, as the first bee starts; the attempt after it records normally.
    trail = FailingTrail(clock, failing_kind="worker.started", times=1)
    deps, queen, warden_id = make_warden_deps(
        clock,
        trail=trail,
        worker_factory=_hanging_worker,
        heartbeat_interval_s=_HUNG_S,
        worker_heartbeat_interval_s=_WORKER_BEAT_S,
    )
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    first = make_assignment(clock=clock)
    await queen.send(make_grant_issued(clock, first, task_id=None, max_sub_bees=1))
    await queen.send(first)
    await settle(lambda: any(bee.runtime_task.done() for bee in warden.sub_bees))
    assert trail.failures == 1
    return _Scene(warden=warden, queen=queen, clock=clock, run_task=run_task, first=first)


async def test_a_resume_supersedes_a_bee_whose_runtime_died_without_ending_the_warden() -> None:
    scene = await _start_with_a_dead_bee()
    [dead] = scene.warden.sub_bees

    # The Queen's resume: a new attempt of the same task, the way she resumes any paused one.
    second = scene.first.model_copy(update={"attempt": scene.first.attempt + 1})
    with capture_logs() as logs:
        await scene.queen.send(second)
        await settle(
            lambda: (
                any(bee.worker_id != dead.worker_id for bee in scene.warden.sub_bees)
                or scene.run_task.done()
            )
        )

    # The Warden is still running and the new attempt's bee is alive on the slot the old one held.
    assert not scene.run_task.done()
    [alive] = scene.warden.sub_bees
    assert alive.worker_id != dead.worker_id and alive.attempt == second.attempt
    await settle(lambda: alive.runtime.state is WorkerState.RUNNING)  # It took its assignment.
    assert [e["event"] for e in logs if e["event"] == "wardens.sub_bee.runtime_failed"] == [
        "wardens.sub_bee.runtime_failed"
    ]
    await scene.warden.stop()
    await asyncio.wait_for(scene.run_task, timeout=_WAIT_S)


async def test_a_warden_stops_cleanly_with_a_bee_whose_runtime_died() -> None:
    scene = await _start_with_a_dead_bee()

    await scene.warden.stop()  # Used to raise the bee's error out of the teardown.
    await asyncio.wait_for(scene.run_task, timeout=_WAIT_S)

    assert scene.warden.state is WardenState.STOPPED
    stopped = await scene.warden._deps.trail.query(TrailQuery(kind="warden.stopped"))
    assert len(stopped) == 1  # The stop ran to its end: the lease is released and it says so.
