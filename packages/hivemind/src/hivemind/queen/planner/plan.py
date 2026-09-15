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

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar

from pydantic import ValidationError

from hivemind.brood_chamber import TaskDraft, TaskGraphDraft
from hivemind.cell import Cell, HoneyClearance
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

__all__ = ["PLANNER_MAX_OUTPUT_TOKENS", "PlanBrief", "PlannerError", "describe_fleet", "plan_goal"]


class PlannerError(ConfigurationError):
    """Raise when a model's plan cannot be turned into a valid TaskGraphDraft.

    Wraps whatever `pydantic.ValidationError` `TaskGraphDraft` or `waggle.messages.Postcondition`
    raised, so a caller catches one typed error regardless of which conversion step failed.
    """

    code: ClassVar[str] = "hivemind.queen.planner_error"


@dataclass(frozen=True, slots=True)
class PlanBrief:
    """What `plan_goal` is asked to plan: the goal, its clearance ceiling and the fleet it has.

    One value rather than three parameters (codingrules 5.1's parameter limit), and the natural
    unit to hand a planner: the text as the human stated it, the data-sensitivity ceiling every
    subtask inherits, and the Cells placement will match the plan's needs against.
    """

    goal: str  # The goal text, as the human (or a bee on the human's behalf) stated it.
    clearance: HoneyClearance  # Every planned subtask's own clearance label (the goal's ceiling).
    cells: Sequence[Cell] | None = None  # The attached Wardens' Cells; None omits hot state.


async def plan_goal(
    brief: PlanBrief,
    bound: BoundModel,
    *,
    gate: CallGate,
    observer: LadderObserver | None = None,
) -> TaskGraphDraft:
    """Decompose `brief.goal` into a validated TaskGraphDraft, through `bound`.

    Args:
        brief: The goal, its clearance ceiling and, when known, the Cells the Hive can place
            work on; the last is rendered by `describe_fleet` into the prompt's hot-state
            section so the plan's needs fit a Cell that exists (None omits the section; an
            empty sequence says so to the model).
        bound: The model binding to plan with; typically `deps.bound_for(ModelSlot.QUEEN)`.
        gate: The seat meter the call passes through.
        observer: Who to tell about a ladder step-down; `NullLadderObserver()` when omitted.

    Returns:
        A validated TaskGraphDraft: acyclic, unique keys, every subtask carrying acceptance.

    Raises:
        PlannerError: The model's plan could not be turned into a valid TaskGraphDraft (a cycle,
            a duplicate key, an unknown `depends_on`, or a malformed acceptance criterion).
        hivemind.llm.errors.MalformedOutputError: Every rung of the structured-output ladder was
            exhausted without a schema-valid reply.
    """
    sections: dict[SectionLabel, str] = {SectionLabel.USER: brief.goal}
    if brief.cells is not None:
        # decompose_goal.md promises "a rough summary of the fleet's capacity" under hot state;
        # without it a model guesses (a Linux-only plan on a Windows Hive Stand never places).
        sections[SectionLabel.HOT_STATE] = describe_fleet(brief.cells)
    system = render(PromptName.DECOMPOSE_GOAL, sections=sections)
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
        return _to_graph_draft(result.value, brief.clearance)
    except ValidationError as exc:
        raise PlannerError(f"The planned graph for {brief.goal[:80]!r} is invalid: {exc}") from exc


def describe_fleet(cells: Sequence[Cell]) -> str:
    """Summarise the Cells the Hive can place work on, for the planner's hot-state section.

    One line per Cell with the facts a plan is judged against: the OS family
    `queen.placement.decide` matches `TaskNeeds.os` on, and the architecture, shell and Python a
    command criterion may assume. An empty fleet is said plainly, so the model keeps needs at
    their defaults instead of guessing.

    Args:
        cells: The Cells behind every attached Warden, in any order.

    Returns:
        A short block of text; `plan_goal` puts it in the hot-state section when given `cells`.
    """
    if not cells:
        return (
            "Fleet: no Cell is attached yet. Set no os and leave every other need at its default."
        )
    lines = [
        "Fleet: the Cells the Hive can place work on right now. A subtask's needs must fit one of "
        "them, and a command criterion must run on it as a plain argument list, without a shell."
    ]
    for cell in cells:
        caps = cell.capabilities
        python = f"python {caps.python_version}" if caps.python_version else "no python"
        lines.append(
            f"- {cell.name}: os {caps.os.value}, {caps.arch}, shell {caps.shell}, {python}"
        )
    return "\n".join(lines)


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
