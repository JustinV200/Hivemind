"""Tests for hivemind.queen.queen.Queen: submit_goal and dispatching a ready task.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_results.py, test_queen_alarms.py, test_queen_questions.py, test_queen_liveness.py,
    test_queen_supervisor.py and test_queen_invariants.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.queen, .dispatcher, .planner.plan for the modules under test.
"""

from __future__ import annotations

from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import PheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.queen import Queen


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


async def _kinds(trail: PheromoneTrail) -> list[str]:
    """Return every recorded trail event's own kind, oldest first."""
    return [event.kind for event in await trail.query(TrailQuery())]


async def test_submit_goal_persists_the_plan_and_records_queen_planned() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)

    task = await deps.chamber.get(goal_id)
    assert task.spec.title == "Root task"
    assert "queen.planned" in await _kinds(deps.trail)
    await warden_end.close()


async def test_submit_goal_dispatches_the_ready_task_grant_then_assignment_in_order() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)

    assignment = await warden_end.wait_for_assignment()
    assert warden_end.received_kinds == ["grant", "assignment"]
    assert assignment.task_id == goal_id
    task = await deps.chamber.get(goal_id)
    assert task.status is TaskStatus.RUNNING
    assert task.warden_id == link.warden_id
    assert task.cell_id == link.cell.id
    await warden_end.close()


async def test_submit_goal_records_queen_assigned_for_the_dispatched_task() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)

    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()

    events = await deps.trail.query(TrailQuery())
    assigned = [event for event in events if event.kind == "queen.assigned"]
    assert len(assigned) == 1
    assert assigned[0].subject_id == goal_id
    await warden_end.close()
