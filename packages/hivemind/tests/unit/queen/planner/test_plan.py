"""Tests for hivemind.queen.planner.plan.plan_goal: a goal, through a model, into a TaskGraphDraft.

Fits into the Hive:
    Mirrors src/hivemind/queen/planner/plan.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.planner.plan for the module under test.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from builders.cells import make_cell
from builders.llm import make_bound
from builders.queen import plan_responder

from hivemind.cell import HoneyClearance
from hivemind.llm import DirectCallGate, FakeLLMProvider
from hivemind.llm.errors import MalformedOutputError
from hivemind.queen.planner import PlanBrief, PlannerError, describe_fleet, plan_goal


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


def _plan_with_command_lacking_argv(goal: str) -> dict[str, object]:
    plan = _valid_plan(goal)
    child = plan["tasks"][1]  # type: ignore[index]
    child["acceptance"][0]["argv"] = []  # A command check with no command: never runnable.
    return plan


def _plan_with_rubric_lacking_expected(goal: str) -> dict[str, object]:
    plan = _valid_plan(goal)
    root = plan["tasks"][0]  # type: ignore[index]
    root["acceptance"][0] = {
        "kind": "JUDGE_RUBRIC",
        "subject": "the message",
        "argv": [],
        "expected": None,  # A rubric with no question: nothing for a judge to answer.
    }
    return plan


def _plan_with_uncheckable_rubric(goal: str) -> dict[str, object]:
    plan = _valid_plan(goal)
    root = plan["tasks"][0]  # type: ignore[index]
    root["acceptance"][0] = {
        "kind": "JUDGE_RUBRIC",
        "subject": "the message",
        "argv": [],
        "expected": "Is the message written in the first person?",  # Well-formed, yet uncheckable.
    }
    return plan


def _cyclic_plan(goal: str) -> dict[str, object]:
    plan = _valid_plan(goal)
    plan["tasks"][0]["depends_on"] = ["child"]  # type: ignore[index]  # root <-> child now cycles.
    return plan


async def test_plan_goal_converts_a_valid_scripted_plan_into_a_task_graph_draft() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_valid_plan))
    bound = make_bound(provider=provider)

    draft = await plan_goal(
        PlanBrief("Write three haiku about bees.", HoneyClearance.C1),
        bound,
        gate=DirectCallGate(),
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
            PlanBrief("Write three haiku about bees.", HoneyClearance.C1),
            bound,
            gate=DirectCallGate(),
        )


async def test_plan_goal_refuses_a_cyclic_plan() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_cyclic_plan))
    bound = make_bound(provider=provider)

    # The cycle is only caught once the schema-valid reply is converted into a TaskGraphDraft, so
    # this is a single PlannerError, not a ladder retry exhaustion.
    with pytest.raises(PlannerError):
        await plan_goal(
            PlanBrief("Write three haiku about bees.", HoneyClearance.C1),
            bound,
            gate=DirectCallGate(),
        )


@pytest.mark.parametrize(
    "build_plan",
    [
        _plan_with_command_lacking_argv,
        _plan_with_rubric_lacking_expected,
        _plan_with_uncheckable_rubric,  # Capping cannot verify a rubric in v0: refused up front.
    ],
)
async def test_plan_goal_retries_a_criterion_breaking_a_postcondition_rule_inside_the_ladder(
    build_plan: Callable[[str], dict[str, object]],
) -> None:
    provider = FakeLLMProvider(responder=plan_responder(build_plan))
    bound = make_bound(provider=provider)

    # PlannedPostcondition re-runs Postcondition's own rules (and requires an argv on a command
    # kind), so a scripted reply that keeps breaking one is a ladder exhaustion after retries,
    # never a single PlannerError raised after the ladder already returned.
    with pytest.raises(MalformedOutputError):
        await plan_goal(
            PlanBrief("Write three haiku about bees.", HoneyClearance.C1),
            bound,
            gate=DirectCallGate(),
        )
    assert len(provider.calls) > 1


async def test_plan_goal_renders_the_fleet_into_the_hot_state_section() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_valid_plan))
    bound = make_bound(provider=provider)

    cells = (make_cell(name="hive-stand"),)
    brief = PlanBrief("Write three haiku about bees.", HoneyClearance.C1, cells)

    await plan_goal(brief, bound, gate=DirectCallGate())

    system = provider.calls[0].system or ""
    assert "<<<hot_state>>>" in system
    assert "Fleet:" in system
    assert "hive-stand" in system


async def test_plan_goal_omits_the_hot_state_section_without_a_fleet() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_valid_plan))
    bound = make_bound(provider=provider)

    brief = PlanBrief("Write three haiku about bees.", HoneyClearance.C1)

    await plan_goal(brief, bound, gate=DirectCallGate())

    assert "<<<hot_state>>>" not in (provider.calls[0].system or "")


def test_describe_fleet_names_each_cell_and_the_os_placement_matches_on() -> None:
    cell = make_cell(name="hive-stand")

    text = describe_fleet((cell,))

    assert "hive-stand" in text
    assert cell.capabilities.os.value in text
    assert cell.capabilities.shell in text


def test_describe_fleet_says_plainly_when_no_cell_is_attached() -> None:
    assert "no Cell is attached" in describe_fleet(())
