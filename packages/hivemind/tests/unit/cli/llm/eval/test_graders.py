"""Tests for hivemind.cli.llm.eval.graders: each pure grader over hand-built evidence.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/graders.py (codingrules section 3: tests/unit mirrors src/).
    No Hive is started: every grader is handed a trail and tasks built by hand, which is the point
    of keeping grading apart from running.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.graders for the module under test.
"""

from __future__ import annotations

from builders.evals import make_evidence
from builders.tasks import make_task, make_task_spec
from builders.trail import make_llm_call, make_trail
from hypothesis import given
from hypothesis import strategies as st

from hivemind.cell import TaskNeeds
from hivemind.cli.llm.eval.goals import GOAL_SET, EvalGoal, ExpectedEffect
from hivemind.cli.llm.eval.graders import (
    grade_alarms,
    grade_cost,
    grade_decomposition,
    grade_effects,
    grade_goal,
    grade_handoff,
    grade_needs,
    grade_question,
    grade_rung,
    grade_tool_calls,
)

_HAIKU, _COMMAND, _ASK, _CHECKPOINT = GOAL_SET


def _fallback(
    reason: str = "RUNG_EXHAUSTED", to_rung: str | None = "JSON_MODE"
) -> tuple[str, dict[str, object]]:
    """Build an `llm.fallback` step as `make_trail` takes it."""
    payload = {"reason": reason, "from_binding": "worker", "to_binding": None, "to_rung": to_rung}
    return ("llm.fallback", {"payload": payload})


def _tasks(needs: TaskNeeds | None = None, count: int = 1) -> tuple[object, ...]:
    """Build `count` pending tasks carrying `needs`."""
    spec = make_task_spec(needs=needs if needs is not None else TaskNeeds())
    return tuple(make_task(spec=spec) for _ in range(count))


# ──────────────────────────────────────────────────────────────────────────────
# Decomposition and TaskNeeds
# ──────────────────────────────────────────────────────────────────────────────


def test_decomposition_passes_for_a_recorded_plan_of_tasks_with_acceptance() -> None:
    events = make_trail(["queen.planned"])

    grade = grade_decomposition(_HAIKU, _tasks(), events)  # type: ignore[arg-type]

    assert (grade.plan_parsed, grade.task_count, grade.without_acceptance) == (True, 1, 0)
    assert grade.passed


def test_decomposition_fails_when_no_plan_was_recorded() -> None:
    grade = grade_decomposition(_HAIKU, _tasks(), ())  # type: ignore[arg-type]

    assert not grade.plan_parsed
    assert not grade.passed


def test_decomposition_fails_for_a_plan_with_no_tasks() -> None:
    grade = grade_decomposition(_HAIKU, (), make_trail(["queen.planned"]))

    assert (grade.plan_parsed, grade.passed) == (False, False)


def test_decomposition_fails_past_the_goals_task_bound() -> None:
    tasks = _tasks(count=_HAIKU.max_tasks + 1)

    grade = grade_decomposition(_HAIKU, tasks, make_trail(["queen.planned"]))  # type: ignore[arg-type]

    assert grade.plan_parsed
    assert not grade.passed


def test_decomposition_counts_a_task_with_no_acceptance_criterion() -> None:
    # TaskSpec validates min_length on acceptance, so a spec without any is built unvalidated:
    # the grader must still catch one that reaches it (a store row written by older code).
    bare = make_task_spec().model_copy(update={"acceptance": ()})
    task = make_task().model_copy(update={"spec": bare})

    grade = grade_decomposition(_HAIKU, (task,), make_trail(["queen.planned"]))

    assert grade.without_acceptance == 1
    assert not grade.passed


def test_needs_scores_one_when_every_task_declares_the_expected_needs() -> None:
    grade = grade_needs(_HAIKU, _tasks(count=2))  # type: ignore[arg-type]

    assert (grade.score, grade.mismatches, grade.passed) == (1.0, (), True)
    assert grade.compared == 10  # Two tasks, five graded fields each.


def test_needs_names_each_mismatching_field_and_lowers_the_score() -> None:
    wrong = TaskNeeds(exoskeleton=True, network_scopes=("example.test",))

    grade = grade_needs(_HAIKU, _tasks(wrong))  # type: ignore[arg-type]

    assert grade.mismatches == ("task1.exoskeleton", "task1.network_scopes")
    assert grade.score == 3 / 5
    assert not grade.passed


def test_needs_with_no_tasks_is_a_failure_not_a_perfect_score() -> None:
    grade = grade_needs(_HAIKU, ())

    assert (grade.compared, grade.score, grade.passed) == (0, 0.0, False)


# ──────────────────────────────────────────────────────────────────────────────
# Effects and the question
# ──────────────────────────────────────────────────────────────────────────────


def test_effects_pass_when_every_expected_file_exists() -> None:
    files = {"haiku_1.txt": "a", "haiku_2.txt": "b", "haiku_3.txt": "c", "extra.txt": "d"}

    assert grade_effects(_HAIKU, files).passed


def test_effects_list_the_missing_files() -> None:
    grade = grade_effects(_HAIKU, {"haiku_1.txt": "a"})

    assert grade.missing == ("haiku_2.txt", "haiku_3.txt")
    assert not grade.passed


def test_effects_check_expected_content_case_insensitively() -> None:
    assert grade_effects(_COMMAND, {"answer.txt": "The answer is 42.\n"}).passed
    assert grade_effects(_COMMAND, {"answer.txt": "forty-one"}).missing == ("answer.txt",)


def test_question_is_not_applicable_to_a_goal_that_asks_nothing() -> None:
    grade = grade_question(_HAIKU, make_trail(["task.blocked", "task.answered"]))

    assert grade.applicable is False
    assert grade.passed is None


def test_question_passes_when_asked_and_answered_and_fails_when_never_asked() -> None:
    asked = grade_question(_ASK, make_trail(["task.blocked", "task.answered"]))
    never = grade_question(_ASK, make_trail(["task.started"]))
    unanswered = grade_question(_ASK, make_trail(["task.blocked"]))

    assert (asked.passed, never.passed, unanswered.passed) == (True, False, False)
    assert (never.asked, unanswered.answered) == (False, False)


# ──────────────────────────────────────────────────────────────────────────────
# Tool-call validity, rung, alarms
# ──────────────────────────────────────────────────────────────────────────────


def test_tool_call_validity_is_one_minus_malformed_rungs_over_calls() -> None:
    events = (*make_trail([_fallback()]), *(make_llm_call() for _ in range(4)))

    grade = grade_tool_calls(events)

    assert (grade.model_calls, grade.malformed, grade.validity_rate) == (4, 1, 0.75)


def test_tool_call_validity_ignores_fallbacks_that_are_not_bad_output() -> None:
    events = (*make_trail([_fallback("PROVIDER_UNAVAILABLE", None)]), make_llm_call())

    assert grade_tool_calls(events).validity_rate == 1.0


def test_tool_call_validity_is_none_without_any_call() -> None:
    assert grade_tool_calls(()).validity_rate is None


@given(calls=st.integers(0, 30), malformed=st.integers(0, 60))
def test_tool_call_validity_rate_stays_within_zero_and_one(calls: int, malformed: int) -> None:
    events = (
        *make_trail([_fallback()] * malformed),
        *(make_llm_call() for _ in range(calls)),
    )

    rate = grade_tool_calls(events).validity_rate

    assert (rate is None) == (calls == 0)
    assert rate is None or 0.0 <= rate <= 1.0


def test_rung_reached_is_the_start_when_nothing_stepped_down() -> None:
    grade = grade_rung("NATIVE", make_trail([_fallback("PROVIDER_UNAVAILABLE", None)]))

    assert (grade.start, grade.reached, grade.stepped_down) == ("NATIVE", "NATIVE", False)


def test_rung_reached_is_the_weakest_rung_any_call_stepped_down_to() -> None:
    events = make_trail([_fallback(to_rung="JSON_MODE"), _fallback(to_rung="PROMPTED")])

    grade = grade_rung("NATIVE", events)

    assert (grade.reached, grade.stepped_down) == ("PROMPTED", True)


def test_rung_reached_never_climbs_back_above_the_start() -> None:
    grade = grade_rung("PROMPTED", make_trail([_fallback(to_rung="JSON_MODE")]))

    assert grade.reached == "PROMPTED"


def test_alarm_rate_is_alarms_raised_per_planned_task() -> None:
    events = make_trail(["alarm.raised", "alarm.raised", "alarm.escalated"])

    grade = grade_alarms(events, 4)

    assert (grade.raised, grade.escalated, grade.rate) == (2, 1, 0.5)


def test_alarm_rate_with_no_tasks_is_zero_not_a_division_error() -> None:
    assert grade_alarms(make_trail(["alarm.raised"]), 0).rate == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Handoff, cost
# ──────────────────────────────────────────────────────────────────────────────


def test_handoff_passes_when_a_forced_checkpoint_was_resumed_and_the_goal_finished() -> None:
    events = make_trail(["memory.checkpoint", "worker.resumed"])

    assert grade_handoff(_CHECKPOINT, events, True).passed is True


def test_handoff_fails_when_a_forced_checkpoint_never_happened() -> None:
    grade = grade_handoff(_CHECKPOINT, (), True)

    assert (grade.applicable, grade.passed) == (True, False)


def test_handoff_fails_when_the_checkpoint_was_not_resumed_or_the_goal_failed() -> None:
    not_resumed = grade_handoff(_CHECKPOINT, make_trail(["memory.checkpoint"]), True)
    unfinished = grade_handoff(
        _CHECKPOINT, make_trail(["memory.checkpoint", "worker.resumed"]), False
    )

    assert (not_resumed.passed, unfinished.passed) == (False, False)


def test_handoff_is_not_applicable_without_a_checkpoint_the_goal_did_not_force() -> None:
    grade = grade_handoff(_HAIKU, (), True)

    assert (grade.applicable, grade.passed) == (False, None)


def test_handoff_applies_to_any_goal_whose_hive_checkpointed_on_its_own() -> None:
    grade = grade_handoff(_HAIKU, make_trail(["memory.checkpoint"]), True)

    assert (grade.applicable, grade.passed) == (True, False)


def test_cost_counts_worker_steps_sums_priced_cost_and_flags_unpriced_calls() -> None:
    events = (
        make_llm_call(slot="QUEEN", cost_usd=0.25),
        make_llm_call(slot="WORKER", cost_usd=0.5),
        make_llm_call(slot="WORKER", priced=False),
    )

    grade = grade_cost(events, 12.5)

    assert (grade.steps, grade.cost_usd, grade.unpriced_calls, grade.wall_time_s) == (
        2,
        0.75,
        1,
        12.5,
    )


def test_cost_clamps_a_negative_elapsed_time_to_zero() -> None:
    assert grade_cost((), -1.0).wall_time_s == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# grade_goal
# ──────────────────────────────────────────────────────────────────────────────


def _clean_haiku_evidence(**overrides: object) -> object:
    """Evidence of a clean haiku run: planned, one task, three files, four model calls."""
    events = (
        *make_trail(["queen.planned"]),
        make_llm_call(slot="QUEEN"),
        *(make_llm_call(slot="WORKER") for _ in range(3)),
    )
    files = {f"haiku_{n}.txt": "bees" for n in (1, 2, 3)}
    return make_evidence(tasks=_tasks(), events=events, files=files, **overrides)


def test_grade_goal_passes_a_clean_run_on_every_column() -> None:
    result = grade_goal(_HAIKU, _clean_haiku_evidence())  # type: ignore[arg-type]

    assert result.passed
    assert result.slot_calls == {"QUEEN": 1, "WORKER": 3}
    assert result.cost.steps == 3


def test_grade_goal_fails_a_run_that_did_not_succeed_even_if_the_files_exist() -> None:
    result = grade_goal(_HAIKU, _clean_haiku_evidence(succeeded=False))  # type: ignore[arg-type]

    assert not result.passed


def test_grade_goal_fails_a_run_that_errored_and_carries_the_error() -> None:
    evidence = make_evidence(succeeded=False, error="ValueError: no provider")

    result = grade_goal(_HAIKU, evidence)

    assert (result.passed, result.error) == (False, "ValueError: no provider")
    assert result.decomposition.task_count == 0


def test_grade_goal_lets_a_column_that_does_not_apply_pass() -> None:
    result = grade_goal(_HAIKU, _clean_haiku_evidence())  # type: ignore[arg-type]

    assert result.question.passed is None
    assert result.handoff.passed is None
    assert result.passed


def test_grade_goal_fails_on_a_wrong_effect() -> None:
    goal = EvalGoal(name="x", text="t", effects=(ExpectedEffect("a.txt", "42"),), summary="s")
    evidence = make_evidence(tasks=_tasks(), files={"a.txt": "41"})

    assert not grade_goal(goal, evidence).passed
