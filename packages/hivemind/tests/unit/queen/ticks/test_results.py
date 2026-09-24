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

from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
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
from waggle.messages.task import ScoutReport, TaskOutcome, TaskResult


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


async def _running_task() -> tuple[QueenDeps, WardenLink, TaskId]:
    """Submit a one-task goal and return its deps, its Warden link and its RUNNING task's id."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
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
