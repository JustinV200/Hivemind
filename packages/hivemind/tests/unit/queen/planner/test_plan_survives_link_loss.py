"""Tests for hivemind.queen.planner.plan: a goal's promise to survive a lost link.

The operator can say a goal's work must keep going if its device loses the link to the Queen
(`PlanBrief.survives_link_loss`), and a plan can say so for one subtask. The Queen's rung decisions
read the flag off every task placed on or pinned to a device, so what these tests hold fixed is
how the two sources combine: the operator's promise reaches every planned task whatever the model
chose, a model's mark on a subtask survives an operator who said nothing, and neither removes the
other.

Fits into the Hive:
    Mirrors src/hivemind/queen/planner/plan.py (codingrules section 3), split by feature from
    test_plan.py (14.2). The model is a `FakeLLMProvider` answering with a scripted plan.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.planner.plan for the module under test.
    - hivemind.queen.swarm_gate.rungs for the reader of the flag.
"""

from __future__ import annotations

import pytest
from builders.llm import make_bound
from builders.queen import plan_responder

from hivemind.brood_chamber import TaskGraphDraft
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.llm import DirectCallGate, FakeLLMProvider, Responder
from hivemind.queen.planner import PlanBrief, plan_goal


def _plan(marked: tuple[bool, bool]) -> Responder:
    """A model that answers with two subtasks carrying `marked` as their own choice of the flag."""

    def build(goal: str) -> dict[str, object]:
        def task(key: str, survives: bool, depends_on: list[str]) -> dict[str, object]:
            return {
                "key": key,
                "title": key,
                "objective": f"Do {key} for: {goal}",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": f"{key}.txt", "argv": [], "expected": None}
                ],
                "needs": {"survives_link_loss": survives},
                "clearance": "C1",
                "depends_on": depends_on,
            }

        return {"tasks": [task("root", marked[0], []), task("child", marked[1], ["root"])]}

    return plan_responder(build)


async def _planned(brief: PlanBrief, marked: tuple[bool, bool]) -> TaskGraphDraft:
    """Plan `brief` against a model that answers with a plan marked as `marked`."""
    provider = FakeLLMProvider(responder=_plan(marked))
    return await plan_goal(brief, make_bound(provider=provider), gate=DirectCallGate())


@pytest.mark.parametrize("marked", [(False, False), (True, False), (True, True)])
async def test_the_operators_promise_reaches_every_subtask_whatever_the_model_chose(
    marked: tuple[bool, bool],
) -> None:
    brief = PlanBrief("Mirror the backups.", HoneyClearance.C1, survives_link_loss=True)

    draft = await _planned(brief, marked)

    assert [task.needs.survives_link_loss for task in draft.tasks] == [True, True]


@pytest.mark.parametrize(
    ("marked", "expected"),
    [
        ((False, False), [False, False]),
        ((True, False), [True, False]),
        ((True, True), [True, True]),
    ],
)
async def test_a_subtask_the_model_marked_keeps_its_mark_when_the_operator_said_nothing(
    marked: tuple[bool, bool], expected: list[bool]
) -> None:
    brief = PlanBrief("Mirror the backups.", HoneyClearance.C1)

    draft = await _planned(brief, marked)

    # Off by default: nothing is promised for a subtask nobody marked.
    assert [task.needs.survives_link_loss for task in draft.tasks] == expected


async def test_the_promise_and_a_requested_tier_are_both_applied_to_every_subtask() -> None:
    brief = PlanBrief(
        "Mirror the backups.",
        HoneyClearance.C1,
        comb_shield=CombShieldLevel.PROPOLIS,
        survives_link_loss=True,
    )

    draft = await _planned(brief, (False, False))

    assert [(t.needs.comb_shield, t.needs.survives_link_loss) for t in draft.tasks] == [
        (CombShieldLevel.PROPOLIS, True),
        (CombShieldLevel.PROPOLIS, True),
    ]


async def test_a_requested_tier_alone_leaves_a_marked_subtask_marked() -> None:
    brief = PlanBrief(
        "Mirror the backups.", HoneyClearance.C1, comb_shield=CombShieldLevel.PROPOLIS
    )

    draft = await _planned(brief, (True, False))

    assert [task.needs.survives_link_loss for task in draft.tasks] == [True, False]
