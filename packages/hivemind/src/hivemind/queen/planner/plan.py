"""Define plan_goal: turn one goal into a validated TaskGraphDraft, through a model.

Roadmap step 3.20: `queen/planner/` "decompose a goal into a TaskGraph with TaskNeeds via
llm/structured.py". `plan_goal` renders `decompose_goal.md` with the goal folded in as the `USER`
section, asks a model for a `hivemind.queen.planner.schema.PlanSchema` through
`hivemind.llm.complete_structured`, and converts the reply into a
`hivemind.brood_chamber.TaskGraphDraft`. `TaskGraphDraft`'s own validators (unique keys, every
`depends_on` naming a real key, no self-dependency, no cycle -- `hivemind.brood_chamber.task.graph.
is_acyclic_edges`) already run at construction, so a cyclic or malformed plan raises a plain
`pydantic.ValidationError` there without this module re-implementing the same check
(`is_acyclic_edges` is `TaskGraphDraft`'s own validator's tool, not a second gate this module calls
again); this module wraps that (and a malformed `PlannedPostcondition`'s own conversion failure)
into one typed `PlannerError` so a caller catches a single name either way.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's planner
    sub-package (which MAY import `hivemind.llm`). Called by `hivemind.queen.queen.Queen.
    submit_goal`. Calls into `hivemind.brood_chamber` (TaskDraft, TaskGraphDraft), `hivemind.cell`
    (HoneyClearance), `hivemind.llm` (CallGate, LLMRequest, LadderObserver, Message, PromptName,
    Role, SectionLabel, complete_structured, render), `hivemind.queen.planner.schema` and
    `waggle.messages` (Postcondition) only.

Key invariants:
    - `plan_goal` never returns a `TaskGraphDraft` whose first task lacks acceptance criteria or
      whose graph cycles: both fail inside `TaskGraphDraft`'s own construction, propagated here as
      `PlannerError`.
    - Every `PlannedTask.key` becomes its `TaskDraft.key` unchanged, so `depends_on` references
      the model wrote resolve without this module renaming anything.

See Also:
    - .claude/roadmap.md step 3.18 for "the planner emits acceptance for every subtask".
    - .claude/roadmap.md step 3.20 for this module's own roadmap bullet.
    - hivemind.queen.planner.schema for PlanSchema, PlannedTask and PlannedPostcondition, the
      model-facing shapes this module converts.
    - hivemind.brood_chamber.task.model for TaskDraft and TaskGraphDraft, the shapes this module
      converts into.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import ValidationError

from hivemind.brood_chamber import TaskDraft, TaskGraphDraft
from hivemind.cell import HoneyClearance
from hivemind.common.errors import ConfigurationError
from hivemind.llm import (
    CallGate,
    LadderObserver,
    LLMRequest,
    Message,
    PromptName,
    Role,
    SectionLabel,
    complete_structured,
    render,
)
from hivemind.llm.slots import BoundModel
from hivemind.queen.planner.schema import PlannedPostcondition, PlannedTask, PlanSchema
from waggle.messages import Postcondition

PLANNER_MAX_OUTPUT_TOKENS = 8_192  # A whole task graph as JSON: generous, still bounded.
_PLANNER_USER_TURN = "Decompose the goal above into a task graph, following the rules given."

__all__ = ["PLANNER_MAX_OUTPUT_TOKENS", "PlannerError", "plan_goal"]


class PlannerError(ConfigurationError):
    """Raise when a model's plan cannot be turned into a valid TaskGraphDraft.

    Wraps whatever `pydantic.ValidationError` `TaskGraphDraft` or `waggle.messages.Postcondition`
    raised, so a caller catches one typed error regardless of which conversion step failed.
    """

    code: ClassVar[str] = "hivemind.queen.planner_error"


async def plan_goal(
    goal: str,
    bound: BoundModel,
    *,
    gate: CallGate,
    observer: LadderObserver | None = None,
    clearance: HoneyClearance,
) -> TaskGraphDraft:
    """Decompose `goal` into a validated TaskGraphDraft, through `bound`.

    Args:
        goal: The goal text, as the human (or a bee on the human's behalf) stated it.
        bound: The model binding to plan with; typically `deps.bound_for(ModelSlot.QUEEN)`.
        gate: The seat meter the call passes through.
        observer: Who to tell about a ladder step-down; `NullLadderObserver()` when omitted.
        clearance: Every planned subtask's own clearance label (the goal's own ceiling).

    Returns:
        A validated TaskGraphDraft: acyclic, unique keys, every subtask carrying acceptance.

    Raises:
        PlannerError: The model's plan could not be turned into a valid TaskGraphDraft (a cycle,
            a duplicate key, an unknown `depends_on`, or a malformed acceptance criterion).
        hivemind.llm.errors.MalformedOutputError: Every rung of the structured-output ladder was
            exhausted without a schema-valid reply.
    """
    system = render(PromptName.DECOMPOSE_GOAL, sections={SectionLabel.USER: goal})
    request = LLMRequest(
        slot=bound.slot,
        system=system,
        messages=(Message.text(Role.USER, _PLANNER_USER_TURN),),
        max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS,
    )
    # External await: one model call, latency class seconds to tens of seconds for a whole plan;
    # the ladder itself retries and steps down rungs on a malformed reply.
    result = await complete_structured(bound, request, PlanSchema, gate=gate, observer=observer)
    try:
        return _to_graph_draft(result.value, clearance)
    except ValidationError as exc:
        raise PlannerError(f"The planned graph for {goal[:80]!r} is invalid: {exc}") from exc


def _to_graph_draft(plan: PlanSchema, clearance: HoneyClearance) -> TaskGraphDraft:
    """Convert every PlannedTask into a TaskDraft; TaskGraphDraft's own validators do the rest."""
    drafts = tuple(_to_task_draft(task, clearance) for task in plan.tasks)
    return TaskGraphDraft(tasks=drafts)


def _to_task_draft(task: PlannedTask, clearance: HoneyClearance) -> TaskDraft:
    """Convert one PlannedTask, and every criterion it carries, into a TaskDraft."""
    return TaskDraft(
        key=task.key,
        title=task.title,
        objective=task.objective,
        acceptance=tuple(_to_postcondition(item) for item in task.acceptance),
        needs=task.needs,
        clearance=min(task.clearance, clearance, key=lambda label: label.rank),
        depends_on=task.depends_on,
    )


def _to_postcondition(item: PlannedPostcondition) -> Postcondition:
    """Convert one PlannedPostcondition into the real, cross-validated wire Postcondition."""
    return Postcondition(
        kind=item.kind, subject=item.subject, argv=item.argv, expected=item.expected
    )
