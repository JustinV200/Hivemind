"""Tests for hivemind.queen.planner.plan.plan_goal: a goal, through a model, into a TaskGraphDraft.

Fits into the Hive:
    Mirrors src/hivemind/queen/planner/plan.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.planner.plan for the module under test.
"""

from __future__ import annotations

import pytest
from builders.llm import make_bound
from builders.queen import plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import DirectCallGate, FakeLLMProvider
from hivemind.llm.errors import MalformedOutputError
from hivemind.queen.planner import PlannerError, plan_goal


def _valid_plan(goal: str) -> dict[str, object]:
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
            },
            {
                "key": "child",
                "title": "Child task",
                "objective": "Depends on the root task.",
                "acceptance": [
                    {
                        "kind": "COMMAND_EXITS_ZERO",
                        "subject": "check",
                        "argv": ["true"],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": ["root"],
            },
        ]
    }


def _plan_missing_acceptance(goal: str) -> dict[str, object]:
    plan = _valid_plan(goal)
    plan["tasks"][0]["acceptance"] = []  # type: ignore[index]
    return plan


def _cyclic_plan(goal: str) -> dict[str, object]:
    plan = _valid_plan(goal)
    plan["tasks"][0]["depends_on"] = ["child"]  # type: ignore[index]  # root <-> child now cycles.
    return plan


async def test_plan_goal_converts_a_valid_scripted_plan_into_a_task_graph_draft() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_valid_plan))
    bound = make_bound(provider=provider)

    draft = await plan_goal(
        "Write three haiku about bees.",
        bound,
        gate=DirectCallGate(),
        clearance=HoneyClearance.C1,
    )

    assert [task.key for task in draft.tasks] == ["root", "child"]
    assert draft.tasks[1].depends_on == ("root",)
    assert all(len(task.acceptance) >= 1 for task in draft.tasks)


async def test_plan_goal_refuses_a_plan_with_a_subtask_lacking_acceptance() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_plan_missing_acceptance))
    bound = make_bound(provider=provider)

    # PlannedTask.acceptance itself requires at least one item, so a model that omits it never
    # produces a schema-valid PlanSchema at all: the ladder exhausts every rung's retries.
    with pytest.raises(MalformedOutputError):
        await plan_goal(
            "Write three haiku about bees.",
            bound,
            gate=DirectCallGate(),
            clearance=HoneyClearance.C1,
        )


async def test_plan_goal_refuses_a_cyclic_plan() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_cyclic_plan))
    bound = make_bound(provider=provider)

    # The cycle is only caught once the schema-valid reply is converted into a TaskGraphDraft, so
    # this is a single PlannerError, not a ladder retry exhaustion.
    with pytest.raises(PlannerError):
        await plan_goal(
            "Write three haiku about bees.",
            bound,
            gate=DirectCallGate(),
            clearance=HoneyClearance.C1,
        )
