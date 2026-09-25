"""Unit tests for hivemind.queen.ticks.results: complete_task, fail_task and fail_reason.

Roadmap step 6.10: `complete_task`'s and `fail_task`'s own `scout_report` carry-through, and
`fail_reason`'s infeasible-Scout wording, tested directly here; `test_queen_dispatch.py` and
`test_queen_results.py` cover the same functions end to end, through a real `Queen` tick.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/results.py (codingrules section 3); this is the module's
    first dedicated unit test file.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.results for the module under test.
"""

from __future__ import annotations

from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.queen import Queen
from hivemind.queen.ticks.results import (
    complete_task,
    fail_reason,
    fail_task,
    fail_task_from_result,
)
from waggle.clock import FakeClock
from waggle.ids import TaskId, WardenId, new_task_id, new_warden_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import ScoutReport, TaskOutcome, TaskResult, WorkerRole


def _single_task_plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/done.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def _scout_chain_plan(goal: str) -> dict[str, object]:
    """A Scout, a task after it, one after that, and a sibling that waits on nothing."""

    def planned(key: str, role: str, depends_on: list[str], subject: str) -> dict[str, object]:
        check: dict[str, object] = {
            "kind": "FILE_EXISTS",
            "subject": subject,
            "argv": [],
            "expected": None,
        }
        return {
            "key": key,
            "title": f"The {key} task",
            "objective": f"The {key} part of: {goal}",
            "acceptance": [check],
            "role": role,
            "needs": {},
            "clearance": "C1",
            "depends_on": depends_on,
        }

    return {
        "tasks": [
            planned("scout", "SCOUT", [], "scout-report.json"),
            planned("next", "DRONE", ["scout"], "scratch/next.txt"),
            planned("after", "DRONE", ["next"], "scratch/after.txt"),
            planned("aside", "DRONE", [], "scratch/aside.txt"),
        ]
    }


async def _scout_assigned(warden_end: WardenEnd) -> TaskId:
    """Wait for both ready tasks' assignments and return the Scout's; "aside" is ready too."""
    await warden_end.pump_until(lambda: len(warden_end.assignments) >= 2)
    return next(a.task_id for a in warden_end.assignments if a.role is WorkerRole.SCOUT)


async def _running_task() -> tuple[QueenDeps, WardenLink, TaskId]:
    """Submit a one-task goal and return its deps, its Warden link and its RUNNING task's id."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    task_id = await queen.submit_goal("Do a thing.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    await warden_end.close()
    return deps, link, task_id


def _result(
    task_id: TaskId,
    warden_id: WardenId,
    outcome: TaskOutcome,
    *,
    reason: str = "Done.",
    scout_report: ScoutReport | None = None,
) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        attempt=1,
        outcome=outcome,
        summary="Done.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=warden_id if outcome is not TaskOutcome.CLAIMED else None,
        handoff=None,
        spend=0.0,
        reason=reason,
        scout_report=scout_report,
    )


# ──────────────────────────────────────────────────────────────────────────────
# complete_task
# ──────────────────────────────────────────────────────────────────────────────


async def test_complete_task_carries_the_scout_report_onto_the_succeeded_outcome() -> None:
    deps, link, task_id = await _running_task()
    report = ScoutReport(feasible=True, summary="Found the login form.")
    payload = _result(task_id, link.warden_id, TaskOutcome.SUCCEEDED, scout_report=report)

    await complete_task(deps, [link], payload, link.warden_id)

    task = await deps.chamber.get(task_id)
    assert task.status is TaskStatus.SUCCEEDED
    assert task.outcome is not None
    assert task.outcome.scout_report == report


async def test_complete_task_leaves_scout_report_none_for_a_non_scout_result() -> None:
    deps, link, task_id = await _running_task()
    payload = _result(task_id, link.warden_id, TaskOutcome.SUCCEEDED)

    await complete_task(deps, [link], payload, link.warden_id)

    task = await deps.chamber.get(task_id)
    assert task.outcome is not None
    assert task.outcome.scout_report is None


# ──────────────────────────────────────────────────────────────────────────────
# fail_reason
# ──────────────────────────────────────────────────────────────────────────────


def test_fail_reason_names_an_infeasible_scout() -> None:
    clock = FakeClock()
    report = ScoutReport(feasible=False, summary="The site is down.")
    payload = _result(
        new_task_id(clock),
        new_warden_id(clock),
        TaskOutcome.SUCCEEDED,
        reason="Recon complete.",
        scout_report=report,
    )

    assert fail_reason(payload) == "Scout reported the work infeasible: The site is down."


def test_fail_reason_echoes_the_result_s_own_reason_otherwise() -> None:
    clock = FakeClock()
    payload = _result(
        new_task_id(clock), new_warden_id(clock), TaskOutcome.FAILED, reason="Ran out of budget."
    )

    assert fail_reason(payload) == "Ran out of budget."


def test_fail_reason_ignores_a_feasible_scout_report() -> None:
    # decide() never reaches FAIL_TASK for a feasible report, but fail_reason itself is total:
    # it only special-cases SUCCEEDED-and-infeasible, echoing payload.reason for anything else.
    clock = FakeClock()
    report = ScoutReport(feasible=True, summary="All clear.")
    payload = _result(
        new_task_id(clock),
        new_warden_id(clock),
        TaskOutcome.SUCCEEDED,
        reason="Recon complete.",
        scout_report=report,
    )

    assert fail_reason(payload) == "Recon complete."


# ──────────────────────────────────────────────────────────────────────────────
# fail_task / fail_task_from_result
# ──────────────────────────────────────────────────────────────────────────────


async def test_fail_task_records_the_given_scout_report_on_the_failed_outcome() -> None:
    deps, _link, task_id = await _running_task()
    report = ScoutReport(feasible=False, summary="The site is down.")

    await fail_task(deps, task_id, "Some failure reason.", scout_report=report)

    task = await deps.chamber.get(task_id)
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    assert task.outcome.scout_report == report


async def test_fail_task_defaults_scout_report_to_none() -> None:
    deps, _link, task_id = await _running_task()

    await fail_task(deps, task_id, "Attempts exhausted.")

    task = await deps.chamber.get(task_id)
    assert task.outcome is not None
    assert task.outcome.scout_report is None


async def test_fail_task_from_result_builds_the_reason_and_carries_the_report() -> None:
    deps, link, task_id = await _running_task()
    report = ScoutReport(feasible=False, summary="The site is down.")
    payload = _result(
        task_id,
        link.warden_id,
        TaskOutcome.SUCCEEDED,
        reason="Recon complete.",
        scout_report=report,
    )

    await fail_task_from_result(deps, payload)

    task = await deps.chamber.get(task_id)
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    assert task.outcome.scout_report == report
    assert "infeasible" in task.outcome.summary


async def test_an_infeasible_scout_cancels_every_task_after_it_and_nothing_else() -> None:
    # Arrange: the Scout is dispatched and running; "aside" never waited on it.
    provider = FakeLLMProvider(responder=plan_responder(_scout_chain_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Log in somewhere.", clearance=HoneyClearance.C1)
    scout = await _scout_assigned(warden_end)
    await warden_end.close()
    report = ScoutReport(feasible=False, summary="The login needs a hardware key.")
    payload = _result(scout, link.warden_id, TaskOutcome.SUCCEEDED, scout_report=report)

    await fail_task_from_result(deps, payload)

    statuses = {task.spec.title: task.status for task in await deps.chamber.list(TaskFilter())}
    assert statuses["The scout task"] is TaskStatus.FAILED
    assert statuses["The next task"] is TaskStatus.CANCELLED  # Direct dependent.
    assert statuses["The after task"] is TaskStatus.CANCELLED  # Transitive dependent.
    assert statuses["The aside task"] is not TaskStatus.CANCELLED  # Never waited on the Scout.


async def test_a_task_failing_for_any_other_reason_cancels_nothing() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_scout_chain_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    await queen.submit_goal("Log in somewhere.", clearance=HoneyClearance.C1)
    scout = await _scout_assigned(warden_end)
    await warden_end.close()
    payload = _result(scout, link.warden_id, TaskOutcome.FAILED, reason="Retries exhausted.")

    await fail_task_from_result(deps, payload)

    cancelled = [
        t for t in await deps.chamber.list(TaskFilter()) if t.status is TaskStatus.CANCELLED
    ]
    assert cancelled == []
