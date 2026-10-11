"""Grade one goal run: pure functions from `GoalEvidence` to the nine grade columns.

Roadmap step 8.5 asks for decomposition validity, `TaskNeeds` correctness, tool-call validity
rate, ladder rung reached, Alarm rate, handoff-resume success, step count, cost and wall time.
Each is one function here, reading only the evidence the runner kept (the planned tasks, the
Pheromone Trail events, the files in scratch) and the goal's own expectations, so a grader is
tested with a hand-built value and no Hive. `grade_goal` assembles the columns into one
`GoalResult`. Nothing here reads a clock, a file or the environment.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.llm.eval`. Called by the
    evaluation pipeline after each run. Calls into `hivemind.llm` (Rung, FallbackReason),
    `hivemind.pheromone` (LlmEvent) and this package's goals, evidence and grades only.

Key invariants:
    - Every function is pure and total: an empty trail or no tasks gives a defined grade, never
      an exception, because a model that planned nothing is a result to report, not a crash.
    - Counting is by event `kind` and, for ladder events, by payload enum values only: the trail
      never carries text (codingrules section 12), so no grader can depend on any.
    - A run with `error` set fails, whatever else its partial evidence says.

See Also:
    - hivemind.cli.llm.eval.grades for the models returned.
    - hivemind.llm.ladders.observer for what an `llm.fallback` event records.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from hivemind.brood_chamber import Task
from hivemind.cli.llm.eval.evidence import GoalEvidence
from hivemind.cli.llm.eval.goals import NEEDS_FIELDS, EvalGoal
from hivemind.cli.llm.eval.grades import (
    AlarmGrade,
    DecompositionGrade,
    EffectsGrade,
    GoalResult,
    HandoffGrade,
    NeedsGrade,
    QuestionGrade,
    RunCost,
    RungGrade,
    ToolCallGrade,
)
from hivemind.forage import ModelSlot
from hivemind.llm import FallbackReason, Rung
from hivemind.pheromone import LlmEvent, PheromoneEvent

# Strongest first: a later index is a weaker rung, so the "reached" rung is the largest index.
_RUNG_ORDER: tuple[str, ...] = tuple(rung.value for rung in Rung)
_WORKER_SLOT = ModelSlot.WORKER.value  # LlmEvent.slot of the Drone's tool-loop calls.

__all__ = [
    "grade_alarms",
    "grade_cost",
    "grade_decomposition",
    "grade_effects",
    "grade_goal",
    "grade_handoff",
    "grade_needs",
    "grade_question",
    "grade_rung",
    "grade_tool_calls",
]


def grade_goal(goal: EvalGoal, evidence: GoalEvidence) -> GoalResult:
    """Grade one run against its goal on every column and combine them.

    Args:
        goal: The goal that was run.
        evidence: What the run left behind.

    Returns:
        The full result; `passed` needs a clean run that succeeded with every applicable grade
        passing.
    """
    events = evidence.events
    decomposition = grade_decomposition(goal, evidence.tasks, events)
    needs = grade_needs(goal, evidence.tasks)
    effects = grade_effects(goal, evidence.files)
    question = grade_question(goal, events)
    handoff = grade_handoff(goal, events, evidence.succeeded)
    # Only the columns that judge the work itself decide `passed`; rates and costs are reported.
    column_passes = (
        decomposition.passed,
        needs.passed,
        effects.passed,
        question.passed is not False,
        handoff.passed is not False,
    )
    return GoalResult(
        goal=goal.name,
        succeeded=evidence.succeeded,
        timed_out=evidence.timed_out,
        error=evidence.error,
        decomposition=decomposition,
        needs=needs,
        effects=effects,
        question=question,
        tool_calls=grade_tool_calls(events),
        rung=grade_rung(evidence.start_rung, events),
        alarms=grade_alarms(events, len(evidence.tasks)),
        handoff=handoff,
        cost=grade_cost(events, evidence.elapsed_s),
        slot_calls=_calls_by_slot(events),
        passed=evidence.error is None and evidence.succeeded and all(column_passes),
    )


def grade_decomposition(
    goal: EvalGoal, tasks: Sequence[Task], events: Sequence[PheromoneEvent]
) -> DecompositionGrade:
    """Grade the plan: it was recorded, it made tasks, and each task can be checked."""
    parsed = bool(tasks) and any(event.kind == "queen.planned" for event in events)
    # A task with no acceptance criterion is one no Warden can verify (Capping needs something
    # to check), so it counts against the plan even though the chamber would accept it.
    without = sum(1 for task in tasks if not task.spec.acceptance)
    within_bound = 0 < len(tasks) <= goal.max_tasks
    return DecompositionGrade(
        plan_parsed=parsed,
        task_count=len(tasks),
        without_acceptance=without,
        passed=parsed and without == 0 and within_bound,
    )


def grade_needs(goal: EvalGoal, tasks: Sequence[Task]) -> NeedsGrade:
    """Compare each planned task's `TaskNeeds` with the goal's, field by field."""
    mismatches: list[str] = []
    compared = 0
    for index, task in enumerate(tasks, start=1):
        for name in NEEDS_FIELDS:
            compared += 1
            if getattr(task.spec.needs, name) != getattr(goal.needs, name):
                mismatches.append(f"task{index}.{name}")
    score = (compared - len(mismatches)) / compared if compared else 0.0
    return NeedsGrade(
        compared=compared,
        mismatches=tuple(mismatches),
        score=score,
        passed=bool(compared) and not mismatches,
    )


def grade_effects(goal: EvalGoal, files: Mapping[str, str]) -> EffectsGrade:
    """Check each expected file exists and holds the text the goal expects of it."""
    missing = tuple(
        effect.path
        for effect in goal.effects
        if effect.path not in files
        or (
            effect.contains is not None
            and effect.contains.lower() not in files[effect.path].lower()
        )
    )
    return EffectsGrade(
        expected=tuple(effect.path for effect in goal.effects), missing=missing, passed=not missing
    )


def grade_question(goal: EvalGoal, events: Sequence[PheromoneEvent]) -> QuestionGrade:
    """Grade a goal that must stop to ask: a task blocked, and then it was answered."""
    applicable = goal.answer is not None
    kinds = Counter(event.kind for event in events)
    asked = kinds["task.blocked"] > 0
    answered = kinds["task.answered"] > 0
    return QuestionGrade(
        applicable=applicable,
        asked=asked,
        answered=answered,
        passed=(asked and answered) if applicable else None,
    )


def grade_tool_calls(events: Sequence[PheromoneEvent]) -> ToolCallGrade:
    """Rate how often model output was malformed: ladder rungs that ran out, over model calls."""
    calls = sum(1 for event in events if event.kind == "llm.call")
    malformed = sum(
        1
        for event in events
        if event.kind == "llm.fallback"
        and event.payload.get("reason") == FallbackReason.RUNG_EXHAUSTED.value
    )
    rate = max(0.0, 1.0 - malformed / calls) if calls else None
    return ToolCallGrade(model_calls=calls, malformed=malformed, validity_rate=rate)


def grade_rung(start: str, events: Sequence[PheromoneEvent]) -> RungGrade:
    """Find the weakest rung any call stepped down to; `start` when none stepped down."""
    reached = start
    stepped = False
    for event in events:
        target = event.payload.get("to_rung") if event.kind == "llm.fallback" else None
        if isinstance(target, str) and target in _RUNG_ORDER:
            stepped = True
            if _RUNG_ORDER.index(target) > _RUNG_ORDER.index(reached):
                reached = target
    return RungGrade(start=start, reached=reached, stepped_down=stepped)


def grade_alarms(events: Sequence[PheromoneEvent], task_count: int) -> AlarmGrade:
    """Count Alarms raised and escalated, and the raised rate per planned task."""
    kinds = Counter(event.kind for event in events)
    raised = kinds["alarm.raised"]
    return AlarmGrade(
        raised=raised,
        escalated=kinds["alarm.escalated"],
        rate=raised / task_count if task_count else 0.0,
    )


def grade_handoff(
    goal: EvalGoal, events: Sequence[PheromoneEvent], succeeded: bool
) -> HandoffGrade:
    """Grade handoff-resume: every checkpoint was picked up and the goal still finished."""
    kinds = Counter(event.kind for event in events)
    checkpoints, resumed = kinds["memory.checkpoint"], kinds["worker.resumed"]
    # Applicable when the harness forced a checkpoint, or the Hive checkpointed on its own: a
    # model that overflows its context is exactly the case the Handoff exists for.
    applicable = goal.forces_checkpoint or checkpoints > 0
    passed = (checkpoints > 0 and resumed > 0 and succeeded) if applicable else None
    return HandoffGrade(
        applicable=applicable, checkpoints=checkpoints, resumed=resumed, passed=passed
    )


def grade_cost(events: Sequence[PheromoneEvent], elapsed_s: float) -> RunCost:
    """Sum steps, priced cost and wall time from the run's `llm.call` events."""
    calls = [event for event in events if isinstance(event, LlmEvent) and event.kind == "llm.call"]
    usages = [event.usage for event in calls if event.usage is not None]
    return RunCost(
        steps=sum(1 for event in calls if event.slot == _WORKER_SLOT),
        cost_usd=sum(usage.cost_usd for usage in usages),
        unpriced_calls=sum(1 for usage in usages if not usage.priced),
        wall_time_s=max(0.0, elapsed_s),
    )


def _calls_by_slot(events: Sequence[PheromoneEvent]) -> dict[str, int]:
    """Count `llm.call` events per slot name, the figure measured grades are attributed by."""
    counts: Counter[str] = Counter(
        event.slot
        for event in events
        if isinstance(event, LlmEvent) and event.kind == "llm.call" and event.slot is not None
    )
    return dict(counts)
