"""Tests for hivemind.queen.queen.Queen: handling a Warden's TaskResult.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_dispatch.py, test_queen_alarms.py, test_queen_questions.py,
    test_queen_liveness.py, test_queen_supervisor.py and test_queen_invariants.py. Exercises
    hivemind.queen.ticks.results together with the Queen's own tick.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.results for the module under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from waggle.ids import TaskId, WardenId
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskOutcome, TaskResult


def _two_task_plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/root.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            },
            {
                "key": "child",
                "title": "Child task",
                "objective": "Depends on the root task.",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/child.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": ["root"],
            },
        ]
    }


def _result(task_id: TaskId, warden_id: WardenId, outcome: TaskOutcome) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        attempt=1,
        outcome=outcome,
        summary="Some outcome.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=warden_id if outcome is not TaskOutcome.CLAIMED else None,
        handoff=None,
        spend=0.0,
        reason="Some reason.",
    )


async def test_succeeded_result_completes_the_task_and_dispatches_its_dependant() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_two_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("Two tasks.", clearance=HoneyClearance.C1)
    root_assignment = await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_result(root_assignment.task_id, link.warden_id, TaskOutcome.SUCCEEDED))
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    root_task = await deps.chamber.get(root_assignment.task_id)
    assert root_task.status is TaskStatus.SUCCEEDED
    assert root_task.outcome is not None
    assert root_task.outcome.verified_by == link.warden_id
    assert warden_end.assignments[1].task_id != root_assignment.task_id
    await warden_end.close()


async def test_failed_result_retries_with_attempt_plus_one_up_to_the_limit_then_fails() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_two_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, alarm_attempt_limit=2)
    queen = Queen(deps)
    queen.attach_warden(link)
    await queen.submit_goal("Two tasks.", clearance=HoneyClearance.C1)
    first_assignment = await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    # Attempt 1 fails: the limit (2) has not been reached yet, so the Queen retries. The retry is
    # a wire-level re-send (hivemind.queen.dispatcher.redispatch), so the next attempt number
    # shows up on the fresh TaskAssign, never on the chamber's own Task.attempt (module docstring
    # of hivemind.queen.ticks.results: a RUNNING task's attempt count cannot live in the chamber).
    await warden_end.send(_result(first_assignment.task_id, link.warden_id, TaskOutcome.FAILED))
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)
    retried = await deps.chamber.get(first_assignment.task_id)
    assert retried.status is TaskStatus.RUNNING
    assert warden_end.assignments[1].task_id == first_assignment.task_id
    assert warden_end.assignments[1].attempt == 2

    # Attempt 2 fails: attempts (2) now meets the limit (2), so the Queen fails the task for good.
    await warden_end.send(_result(first_assignment.task_id, link.warden_id, TaskOutcome.FAILED))
    await _wait_until(lambda: _is_terminal(deps, first_assignment.task_id))

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    failed = await deps.chamber.get(first_assignment.task_id)
    assert failed.status is TaskStatus.FAILED
    await warden_end.close()


async def _is_terminal(deps: QueenDeps, task_id: TaskId) -> bool:
    task = await deps.chamber.get(task_id)
    return task.status in (TaskStatus.FAILED, TaskStatus.SUCCEEDED, TaskStatus.CANCELLED)


async def _wait_until(condition: Callable[[], Awaitable[bool]], limit: int = 200) -> None:
    """Yield the event loop until `condition()` (an async callable) is True, or give up."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")
