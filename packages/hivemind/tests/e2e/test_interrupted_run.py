"""End to end: a stopped Hive gives its blocked task back, and the next start asks again.

The whole-system run of 2026-10-03 stopped a local `hive run` with Ctrl+C while its Drone waited
on the human: the teardown retired the Cell, but the goal's task stayed BLOCKED in the Brood
Chamber, bound to a Cell that no longer existed, and its question waited in every inbox for an
answer no bee would ever get. These runs do the same through the real composition (`build_hive`
over the Hive's own SQLite file, the Queen, the Hive Stand's Warden and a real Drone on a real
lease, every model call scripted), and read the Brood Chamber afterwards: the task is PENDING,
placed nowhere, its question withdrawn. A second start over the same file places it afresh, its
fresh Drone asks again, and the answer lets it finish. `hive serve` stops through the same
teardown, after its Entrance, and leaves the same.

Fits into the Hive:
    Whole-Hive scenario (codingrules 14.2, `e2e`). Mirrors no single module: it exercises
    hivemind.cli.compose.hive.run's teardown and hivemind.queen.requeue_unfinished, through both
    of the CLI's roots (`hive run` cancelled as Ctrl+C cancels it, and `hive serve` stopped).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/queen/dispatcher/test_ready_unfinished.py for the same rule at the unit level.
    - .claude/phase-10-handoff.md, the whole-system run's "seen, not changed" notes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.entrance.goals import goal_responder
from builders.entrance.stand import stand_manifest
from e2e.kernel_helpers import wait_until

from hivemind.brood_chamber import AnswerSource, Question, Task, TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.cli.compose.entrance import build_served_hive, serve_hive
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher.ready.unfinished import STOPPED_REASON
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_QUESTION = "Which season?"  # What the scripted Drone asks before it writes its file.
_GOAL = "write a haiku about bees"
_WAIT_S = 30.0  # Generous: each step here takes well under a second.
_RUN_S = 120.0  # The interrupted run's own timeout, never reached: the test stops it first.


async def test_an_interrupted_run_gives_its_blocked_task_back_for_the_next_start(
    tmp_path: Path,
) -> None:
    manifest = load_manifest(stand_manifest(tmp_path), {})
    first = await _build(manifest)
    running = asyncio.ensure_future(_run(first))
    blocked = await _task_in(first, TaskStatus.BLOCKED)

    # Ctrl+C: asyncio.run cancels its main task, and run_hive's teardown runs to its end.
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    after = await first.stores.chamber.get(blocked.id)
    assert after.status is TaskStatus.PENDING
    assert (after.warden_id, after.cell_id, after.bound_tier) == (None, None, None)
    assert await first.stores.chamber.pending_questions() == ()
    withdrawn = TrailQuery(kind="task.question_withdrawn", subject_id=blocked.id)
    [event] = await first.stores.trail.query(withdrawn)
    assert event.payload["reason"] == STOPPED_REASON

    # The next start over the same file places it afresh; its fresh Drone asks again.
    second = await _build(manifest)
    async with run_hive(second):
        asked = await _asked(second, blocked)
        await second.queen.answer_question(
            asked.id, "spring", source=AnswerSource.HUMAN, clearance=HoneyClearance.C2
        )
        finished = await _task_in(second, TaskStatus.SUCCEEDED)

    assert finished.id == blocked.id
    assert asked.id != blocked.pending_question_id  # A new question, not the withdrawn one.


async def test_a_stopped_serve_gives_its_blocked_task_back(tmp_path: Path) -> None:
    manifest = load_manifest(stand_manifest(tmp_path), {})
    # build_served_hive runs its own event loops (the stores), so never inside the test's.
    served = await asyncio.to_thread(
        build_served_hive,
        manifest,
        environ={},
        clock=SystemClock(),
        responders={"fake": goal_responder(_QUESTION)},
    )
    hive = served.hive

    async with serve_hive(served):
        await hive.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)
        blocked = await _task_in(hive, TaskStatus.BLOCKED)

    after = await hive.stores.chamber.get(blocked.id)
    assert after.status is TaskStatus.PENDING and after.cell_id is None
    assert await hive.stores.chamber.pending_questions() == ()


async def _build(manifest: HiveManifest) -> Hive:
    """Build the Hive over `manifest`'s own file, its Drone scripted to ask before it writes."""
    # build_hive runs its own event loops (the stores), so never inside the test's.
    return await asyncio.to_thread(
        build_hive,
        manifest,
        environ={},
        clock=SystemClock(),
        responders={"fake": goal_responder(_QUESTION)},
    )


async def _run(hive: Hive) -> None:
    """Run one goal as `hive run` does, until something stops it."""
    async with run_hive(hive):
        await run_goal(hive, _GOAL, clearance=HoneyClearance.C1, timeout_s=_RUN_S)


async def _task_in(hive: Hive, status: TaskStatus) -> Task:
    """Wait until a task reaches `status`, and return it."""
    found: list[Task] = []

    async def reached() -> bool:
        found[:] = [t for t in await hive.stores.chamber.list(TaskFilter()) if t.status is status]
        return bool(found)

    await wait_until(reached, timeout_s=_WAIT_S)
    return found[0]


async def _asked(hive: Hive, task: Task) -> Question:
    """Wait until `task`'s fresh bee has asked its question, and return the question."""
    asked: list[Question] = []

    async def waiting() -> bool:
        asked[:] = await hive.stores.chamber.pending_questions(task.id)
        return bool(asked)

    await wait_until(waiting, timeout_s=_WAIT_S)
    return asked[0]
