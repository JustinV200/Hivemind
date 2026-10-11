"""Tests for hivemind.cli.llm.eval.goals: the fixed goal set and its selection.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/goals.py (codingrules section 3: tests/unit mirrors src/).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.goals for the module under test.
"""

from __future__ import annotations

import pytest

from hivemind.cell import TaskNeeds
from hivemind.cli.llm.eval.goals import GOAL_SET, NEEDS_FIELDS, UnknownGoalError, select_goals


def test_the_goal_set_holds_the_four_behaviours_the_roadmap_names() -> None:
    assert [goal.name for goal in GOAL_SET] == ["haiku", "command", "ask", "checkpoint"]


def test_goal_names_are_unique_lowercase_identifiers() -> None:
    names = [goal.name for goal in GOAL_SET]

    assert len(set(names)) == len(names)
    assert all(name.isidentifier() and name == name.lower() for name in names)


def test_every_goal_states_the_effects_that_prove_it_worked() -> None:
    for goal in GOAL_SET:
        assert goal.effects, goal.name
        for effect in goal.effects:
            # A relative path inside scratch: never absolute, never climbing out of it.
            assert not effect.path.startswith("/"), effect.path
            assert ".." not in effect.path.split("/"), effect.path


def test_only_the_ask_goal_carries_an_answer_and_only_checkpoint_forces_one() -> None:
    assert [goal.name for goal in GOAL_SET if goal.answer is not None] == ["ask"]
    assert [goal.name for goal in GOAL_SET if goal.forces_checkpoint] == ["checkpoint"]


def test_every_goal_expects_the_plain_terminal_only_needs() -> None:
    for goal in GOAL_SET:
        assert goal.needs == TaskNeeds()


def test_the_graded_needs_fields_are_real_task_needs_fields() -> None:
    assert set(NEEDS_FIELDS) <= set(TaskNeeds.model_fields)


def test_select_goals_without_names_returns_the_whole_set() -> None:
    assert select_goals() == GOAL_SET
    assert select_goals(()) == GOAL_SET


def test_select_goals_keeps_the_sets_own_order_whatever_order_the_names_come_in() -> None:
    selected = select_goals(("ask", "haiku"))

    assert [goal.name for goal in selected] == ["haiku", "ask"]


def test_select_goals_names_the_unknown_goal_and_the_valid_ones() -> None:
    with pytest.raises(UnknownGoalError, match=r"nope.*known: ask, checkpoint, command, haiku"):
        select_goals(("haiku", "nope"))
