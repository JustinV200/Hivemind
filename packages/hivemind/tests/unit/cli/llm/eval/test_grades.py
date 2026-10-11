"""Tests for hivemind.cli.llm.eval.grades: the frozen grade models.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/grades.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.grades for the module under test.
"""

from __future__ import annotations

import pytest
from builders.evals import make_goal_result
from pydantic import ValidationError

from hivemind.cli.llm.eval.grades import GoalResult, NeedsGrade


def test_a_goal_result_round_trips_through_json() -> None:
    result = make_goal_result()

    assert GoalResult.model_validate_json(result.model_dump_json()) == result


def test_a_goal_result_refuses_an_unknown_field() -> None:
    data = make_goal_result().model_dump()
    data["surprise"] = 1

    with pytest.raises(ValidationError):
        GoalResult.model_validate(data)


def test_a_grade_is_frozen() -> None:
    grade = make_goal_result().needs

    with pytest.raises(ValidationError):
        grade.score = 0.0


def test_a_needs_score_outside_zero_to_one_is_refused() -> None:
    with pytest.raises(ValidationError):
        NeedsGrade(compared=1, mismatches=(), score=1.5, passed=True)
