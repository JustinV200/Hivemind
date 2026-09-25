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

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from waggle.ids import TaskId, WardenId
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import ScoutReport, TaskOutcome, TaskResult


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


def _result(
    task_id: TaskId,
    warden_id: WardenId,
    outcome: TaskOutcome,
    attempt: int = 1,
    scout_report: ScoutReport | None = None,
) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        attempt=attempt,
        outcome=outcome,
        summary="Some outcome.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=warden_id if outcome is not TaskOutcome.CLAIMED else None,
        handoff=None,
        spend=0.0,
        reason="Some reason.",
        scout_report=scout_report,
    )


def _scout_then_child_plan(goal: str) -> dict[str, object]:
    """A SCOUT root task, plus a plain child that depends on it (roadmap step 6.10)."""
    return {
        "tasks": [
            {
                "key": "scout",
                "title": "Scout the site",
                "objective": f"Look around before acting on: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scout-report.json",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "role": "SCOUT",
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            },
            {
                "key": "child",
                "title": "Child task",
                "objective": "Depends on the scout's recon.",
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
                "depends_on": ["scout"],
            },
        ]
    }


async def test_succeeded_result_completes_the_task_and_dispatches_its_dependant() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_two_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
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
    await queen.attach_warden(link)
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
    # The result echoes attempt 2 (as a real Warden's does): a FAILED result for attempt 1 arriving
    # now would be stale and only recorded (hivemind.queen.autopilot.table).
    await warden_end.send(
        _result(first_assignment.task_id, link.warden_id, TaskOutcome.FAILED, attempt=2)
    )
    await _wait_until(lambda: _is_terminal(deps, first_assignment.task_id))

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    failed = await deps.chamber.get(first_assignment.task_id)
    assert failed.status is TaskStatus.FAILED
    await warden_end.close()


async def test_an_infeasible_scout_fails_without_retry_and_holds_its_dependent_back() -> None:
    """Roadmap 6.10, through the real Queen tick: FAIL_TASK, never RETRY_TASK; dependent cut.

    Drives autopilot.table._decide_task_result, queen._act_on_task_result and
    ticks.results.fail_task_from_result, as the ordinary FAILED-result test does.
    """
    provider = FakeLLMProvider(responder=plan_responder(_scout_then_child_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, alarm_attempt_limit=2)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Look around a site.", clearance=HoneyClearance.C1)
    scout_assignment = await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    report = ScoutReport(feasible=False, summary="The site requires a login we do not have.")
    await warden_end.send(
        _result(
            scout_assignment.task_id,
            link.warden_id,
            TaskOutcome.SUCCEEDED,  # Acceptance passed (the report file exists); still failed.
            scout_report=report,
        )
    )
    await _wait_until(lambda: _is_terminal(deps, scout_assignment.task_id))

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    scout_task = await deps.chamber.get(scout_assignment.task_id)
    assert scout_task.status is TaskStatus.FAILED
    assert scout_task.outcome is not None
    assert scout_task.outcome.scout_report == report  # The report survives onto the outcome.
    assert "infeasible" in scout_task.outcome.summary
    assert "site requires a login" in scout_task.outcome.summary

    # Never retried: exactly the one, original assignment ever went out for the Scout task.
    scout_wire = [a for a in warden_end.assignments if a.task_id == scout_assignment.task_id]
    assert len(scout_wire) == 1

    # The dependent is never dispatched, and it is cancelled with the Scout's reason, so the goal
    # ends now rather than waiting on a task that could never become ready.
    child_task = next(
        t for t in await deps.chamber.list(TaskFilter()) if t.id != scout_assignment.task_id
    )
    assert child_task.status is TaskStatus.CANCELLED
    assert child_task.outcome is not None
    assert f"Held back by Scout {scout_assignment.task_id}" in child_task.outcome.summary
    assert "site requires a login" in child_task.outcome.summary
    assert len(warden_end.assignments) == 1
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


async def test_a_dependant_of_a_task_that_failed_for_good_is_cancelled_not_left_pending() -> None:
    # Handoff known issue 3: the child could never run, so leaving it PENDING kept `hive run`
    # waiting out its whole timeout on a goal that was already over.
    provider = FakeLLMProvider(responder=plan_responder(_two_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider, alarm_attempt_limit=1)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Two tasks.", clearance=HoneyClearance.C1)
    root = await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_result(root.task_id, link.warden_id, TaskOutcome.FAILED))
    [child] = [
        task
        for task in await deps.chamber.list(TaskFilter(goal_id=goal_id))
        if task.id != root.task_id
    ]
    await _wait_until(lambda: _is_terminal(deps, child.id))
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    cancelled = await deps.chamber.get(child.id)
    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.outcome is not None and root.task_id in cancelled.outcome.summary
    assert len(warden_end.assignments) == 1  # The child was never dispatched.
    await warden_end.close()


async def test_a_finished_tasks_grant_goes_back_to_the_pool_on_the_trail() -> None:
    # The ledger used to keep a finished task's grant for good (renewed on every Heartbeat), so
    # its Cell looked busy and its seats looked taken long after the work was done.
    provider = FakeLLMProvider(responder=plan_responder(_two_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Two tasks.", clearance=HoneyClearance.C1)
    root = await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    await warden_end.send(_result(root.task_id, link.warden_id, TaskOutcome.SUCCEEDED))
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    child = warden_end.assignments[1].task_id
    assert [grant.task_id for grant in deps.ledger.live_grants()] == [child]
    [released] = await deps.trail.query(TrailQuery(kind="forage.revoked"))
    assert released.subject_id == root.grant_id
    assert released.payload["cause"] == "RELEASED"
    assert released.payload["task_id"] == root.task_id
    await warden_end.close()
