"""Define the grade models a goal run is scored into: one small frozen model per measured thing.

The evaluation harness (roadmap step 8.5) scores each goal run on nine things: whether the plan
was valid (decomposition), whether its tasks declared sound `TaskNeeds` (what a task requires from
its Cell), whether the work left the expected effects, whether any question was asked and
answered, how often the model's output was malformed (tool-call validity), how far down the
degradation ladder it had to go (rung reached), how many Alarms (the supervision signal a bee
raises when something is wrong) were raised, whether a Handoff (a resumable snapshot of a task's
memory) was resumed, and what the run cost in steps, dollars and time. Each is a boundary model:
it crosses into the report, so it is a frozen pydantic model with every field described, and
`GoalResult` bundles them for one goal.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Built by the
    graders, collected into `EvalReport`, read by the markdown renderer and the measured-grade
    mapping. Calls into nothing but pydantic.

Key invariants:
    - Every rate is None, never 0.0 or 1.0, when its denominator is zero: "nothing to measure"
      is not a perfect or a failing score.
    - `GoalResult.passed` is the conjunction of every applicable grade's own `passed`, so one
      failing column fails the goal and a not-applicable column never does.

See Also:
    - hivemind.cli.llm.eval.graders for the pure functions that build these.
    - hivemind.cli.llm.eval.report for EvalReport, which holds one GoalResult per goal.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AlarmGrade",
    "DecompositionGrade",
    "EffectsGrade",
    "GoalResult",
    "HandoffGrade",
    "NeedsGrade",
    "QuestionGrade",
    "RunCost",
    "RungGrade",
    "ToolCallGrade",
]

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class DecompositionGrade(BaseModel):
    """Whether the Queen's plan parsed into a sound set of tasks."""

    model_config = _FROZEN

    plan_parsed: bool = Field(description="A plan was recorded and produced at least one task.")
    task_count: int = Field(ge=0, description="How many tasks the plan holds.")
    without_acceptance: int = Field(
        ge=0, description="Tasks with no acceptance criterion a Warden could check."
    )
    passed: bool = Field(description="Parsed, within the goal's task bound, all with acceptance.")


class NeedsGrade(BaseModel):
    """How closely the planned tasks' `TaskNeeds` match what a sound plan declares."""

    model_config = _FROZEN

    compared: int = Field(ge=0, description="Field comparisons made across all tasks.")
    mismatches: tuple[str, ...] = Field(description="Each mismatch as 'task.field'.")
    score: float = Field(ge=0, le=1, description="Matching comparisons over all comparisons.")
    passed: bool = Field(description="True when every comparison matched and there was one.")


class EffectsGrade(BaseModel):
    """Whether the expected files exist, with the expected content, when the goal ended."""

    model_config = _FROZEN

    expected: tuple[str, ...] = Field(description="Every path the goal had to leave behind.")
    missing: tuple[str, ...] = Field(description="Expected paths absent or with wrong content.")
    passed: bool = Field(description="True when nothing is missing.")


class QuestionGrade(BaseModel):
    """Whether the goal's question was asked and answered, for a goal that must provoke one."""

    model_config = _FROZEN

    applicable: bool = Field(description="The goal is one that must ask a question.")
    asked: bool = Field(description="A task blocked on a question.")
    answered: bool = Field(description="The question was answered and the task unblocked.")
    passed: bool | None = Field(description="Asked and answered; None when not applicable.")


class ToolCallGrade(BaseModel):
    """How often a model call came back malformed, from the ladders' own fallback events."""

    model_config = _FROZEN

    model_calls: int = Field(ge=0, description="`llm.call` events: every model call made.")
    malformed: int = Field(
        ge=0, description="`llm.fallback` events where a rung ran out of retries on bad output."
    )
    validity_rate: float | None = Field(
        description="1 minus malformed over calls, floored at 0; None with no calls."
    )


class RungGrade(BaseModel):
    """How far down the structured-output ladder the run had to go."""

    model_config = _FROZEN

    start: str = Field(description="The rung the planner's model starts on, from its capabilities.")
    reached: str = Field(description="The weakest rung any call stepped down to, else `start`.")
    stepped_down: bool = Field(description="Any ladder step-down happened during the run.")


class AlarmGrade(BaseModel):
    """How many Alarms the run raised, per task."""

    model_config = _FROZEN

    raised: int = Field(ge=0, description="`alarm.raised` events.")
    escalated: int = Field(ge=0, description="`alarm.escalated` events: handed up the chain.")
    rate: float = Field(ge=0, description="Alarms raised per planned task (0 with no tasks).")


class HandoffGrade(BaseModel):
    """Whether a checkpointed worker was resumed from its Handoff."""

    model_config = _FROZEN

    applicable: bool = Field(description="A checkpoint was forced, or one happened anyway.")
    checkpoints: int = Field(ge=0, description="`memory.checkpoint` events: Handoffs written.")
    resumed: int = Field(ge=0, description="`worker.resumed` events: bees that picked one up.")
    passed: bool | None = Field(description="Resumed and finished; None when not applicable.")


class RunCost(BaseModel):
    """What the run cost: steps, money and time."""

    model_config = _FROZEN

    steps: int = Field(ge=0, description="Worker-slot model calls: turns of the tool loop.")
    cost_usd: float = Field(ge=0, description="Sum of every `llm.call`'s priced cost.")
    unpriced_calls: int = Field(ge=0, description="Calls with no price: their cost is unknown.")
    wall_time_s: float = Field(ge=0, description="Seconds from submission to the end of polling.")


class GoalResult(BaseModel):
    """One goal's whole graded result: every grader's column, plus how the run ended."""

    model_config = _FROZEN

    goal: str = Field(description="The goal's name.")
    succeeded: bool = Field(description="Every task SUCCEEDED before the timeout.")
    timed_out: bool = Field(description="The goal's wall-clock budget ran out first.")
    error: str | None = Field(description="Why the run could not complete, else None.")
    decomposition: DecompositionGrade = Field(description="Plan validity.")
    needs: NeedsGrade = Field(description="TaskNeeds correctness.")
    effects: EffectsGrade = Field(description="Expected files.")
    question: QuestionGrade = Field(description="Ask and answer.")
    tool_calls: ToolCallGrade = Field(description="Tool-call validity rate.")
    rung: RungGrade = Field(description="Ladder rung reached.")
    alarms: AlarmGrade = Field(description="Alarm rate.")
    handoff: HandoffGrade = Field(description="Handoff-resume success.")
    cost: RunCost = Field(description="Steps, cost and wall time.")
    slot_calls: dict[str, int] = Field(description="`llm.call` count per slot name, for scoring.")
    passed: bool = Field(description="Succeeded, no error, and every applicable grade passed.")
