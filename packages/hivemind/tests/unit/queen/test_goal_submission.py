"""Tests for hivemind.queen.goal_submission.submit_goal: the planner's Honey consultation.

Fits into the Hive:
    Mirrors src/hivemind/queen/goal_submission.py (codingrules section 3). Planning itself is
    covered by tests/unit/queen/planner/test_plan.py and the Queen's own submit path by
    tests/unit/queen/test_queen_dispatch.py; this module covers what roadmap step 7.9 adds: the
    Honey the Queen consults before planning, against a real SQLite Honey Store.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.goal_submission for the module under test.
"""

from __future__ import annotations

from pathlib import Path

from builders.honey import make_nectar_submission
from builders.house_bee import HoneyHarness, open_honey_access
from builders.queen import make_queen_deps, plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.goal_submission import GoalTerms, submit_goal
from waggle.clock import FakeClock

_GOAL = "Find which port the widget service listens on."
_FACT = b"The widget service listens on port 48213."
_RETRIEVED_OPEN = "\n<<<retrieved>>>\n"  # The rendered section's own opening delimiter line.


def _one_task_plan(goal: str) -> dict[str, object]:
    """A valid one-task plan for `goal`, at C2 so a C2 goal keeps it whole."""
    return {
        "tasks": [
            {
                "key": "answer",
                "title": "Answer the question",
                "objective": goal,
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": "answer.txt", "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": "C2",
                "depends_on": [],
            }
        ]
    }


async def _setup(
    tmp_path: Path, *, with_honey: bool = True
) -> tuple[QueenDeps, WardenLink, FakeLLMProvider, HoneyHarness]:
    """A Queen scripted to plan `_one_task_plan`, with a real Honey Store holding one finding."""
    clock = FakeClock()
    harness = await open_honey_access(tmp_path, clock)
    await harness.access.intake.submit(make_nectar_submission(clock=clock, content=_FACT))
    await harness.access.ripener.run_pass()
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    honey = harness.access if with_honey else None
    deps, link, _end = make_queen_deps(clock, fake_provider=provider, honey=honey)
    return deps, link, provider, harness


async def test_submit_goal_shows_the_planner_what_honey_knows_about_the_goal(
    tmp_path: Path,
) -> None:
    deps, link, provider, _harness = await _setup(tmp_path)

    goal_id = await submit_goal(deps, [link], _GOAL, GoalTerms(HoneyClearance.C2))

    system = provider.calls[0].system or ""
    assert _RETRIEVED_OPEN in system
    assert "48213" in system
    consulted = await deps.trail.query(TrailQuery(kind="queen.honey_consulted"))
    # The plan's consultation, then the pre-check dispatch_ready runs for the planned task.
    assert [event.payload["stage"] for event in consulted] == ["plan", "assign"]
    plan_event = consulted[0]
    assert plan_event.subject_id == goal_id
    hits = plan_event.payload["hits"]
    assert isinstance(hits, int) and hits >= 1


async def test_submit_goal_reads_no_honey_above_the_goals_clearance(tmp_path: Path) -> None:
    deps, link, provider, harness = await _setup(tmp_path)
    royal = make_nectar_submission(
        content=b"The widget service's admin port is 9000.", declared=HoneyClearance.C2
    )
    await harness.access.intake.submit(royal)
    await harness.access.ripener.run_pass()

    await submit_goal(deps, [link], _GOAL, GoalTerms(HoneyClearance.C1))

    system = provider.calls[0].system or ""
    assert "48213" in system  # The C1 finding is shown.
    assert "9000" not in system  # The C2 one never is.


async def test_submit_goal_plans_as_before_with_no_honey_store(tmp_path: Path) -> None:
    deps, link, provider, _harness = await _setup(tmp_path, with_honey=False)

    await submit_goal(deps, [link], _GOAL, GoalTerms(HoneyClearance.C2))

    assert _RETRIEVED_OPEN not in (provider.calls[0].system or "")
    assert await deps.trail.query(TrailQuery(kind="queen.honey_consulted")) == ()
