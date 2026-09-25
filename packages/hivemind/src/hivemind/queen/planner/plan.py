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
into one typed `PlannerError` so a caller catches a single name either way. `PlanBrief.scratch_root`
(roadmap step 5.0b), when given, is threaded to `complete_structured` as a pydantic validation
`context`, the one way `hivemind.queen.planner.schema.PlannedTask`'s own leaves-vs-scratch rule can
see the Hive Stand's own scratch root without this module (or the ladder) knowing what a "lease
directory" is. `PlanBrief.honey` (roadmap step 7.9) carries what the Queen's consultation of the
Honey Store (the Hive's ripened, searchable knowledge) found for the goal; `_build_request`
renders it into the prompt's RETRIEVED section, each hit as the same block `hivemind.memory.
assemble` renders one as, under the same preamble that labels it reference data, never
instructions (codingrules section 15).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's planner
    sub-package (which MAY import `hivemind.llm`). Called by `hivemind.queen.goal_submission.
    submit_goal`. Calls into `hivemind.brood_chamber` (TaskDraft, TaskGraphDraft, GoalRequestId),
    `hivemind.cell` (HoneyClearance, the tier and isolation enums, TaskNeeds), `hivemind.llm`
    (CallGate, LLMRequest, LadderObserver, Message, PromptName, Role, SectionLabel,
    complete_structured, render), `hivemind.memory` (RETRIEVED_PREAMBLE, render_item,
    RetrievedItem, is_refused, ITEM_CAP_CHARS), `hivemind.queen.planner.schema` and
    `waggle.messages` (Postcondition) only.

Key invariants:
    - `plan_goal` never returns a `TaskGraphDraft` whose first task lacks acceptance criteria or
      whose graph cycles: both fail inside `TaskGraphDraft`'s own construction, propagated here as
      `PlannerError`.
    - A tier the goal's request named (`PlanBrief.comb_shield`) becomes every draft's own, and a
      Night Veil one forces REQUIRED isolation; with none, the model's own needs stand.
    - Every `PlannedTask.key` becomes its `TaskDraft.key` unchanged, so `depends_on` references
      the model wrote resolve without this module renaming anything; every `PlannedTask.leaves`
      entry becomes its `TaskDraft.leaves` entry unchanged, for the same reason. `PlannedTask.
      role` (roadmap steps 6.9/6.10) becomes its `TaskDraft.role` unchanged too: `PlannedTask`'s
      own validators already confirmed it is plannable, so this module only carries it, never
      re-checks it.
    - A retrieved hit labelled above the goal's clearance, or tainted, never reaches the prompt
      (the Queen's consultation already filtered by clearance; this is defence in depth); every
      hit shown was scanned first and renders under its verdict, and no RETRIEVED section is
      rendered at all when nothing was retrieved.

See Also:
    - .claude/roadmap.md step 3.18 for "the planner emits acceptance for every subtask".
    - .claude/roadmap.md step 3.20 for this module's own roadmap bullet.
    - .claude/roadmap.md step 5.0b for "the plan declares what stays".
    - hivemind.queen.planner.schema for PlanSchema, PlannedTask and PlannedPostcondition, the
      model-facing shapes this module converts.
    - hivemind.brood_chamber.task.model for TaskDraft and TaskGraphDraft, the shapes this module
      converts into.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from pydantic import ValidationError

from hivemind.brood_chamber import TaskDraft, TaskGraphDraft
from hivemind.brood_chamber.task import GoalRequestId
from hivemind.cell import Cell, CombShieldLevel, HoneyClearance, Isolation, RequestOrigin, TaskNeeds
from hivemind.common.errors import ConfigurationError
from hivemind.llm import (
    CallGate,
    LadderObserver,
    LadderOptions,
    LLMRequest,
    Message,
    PromptName,
    Role,
    SectionLabel,
    complete_structured,
    render,
)
from hivemind.llm.slots import BoundModel
from hivemind.memory import (
    ITEM_CAP_CHARS,
    RETRIEVED_PREAMBLE,
    RetrievedItem,
    is_refused,
    render_item,
)
from hivemind.queen.planner.schema import PlannedPostcondition, PlannedTask, PlanSchema
from waggle.messages import Postcondition

PLANNER_MAX_OUTPUT_TOKENS = 8_192  # A whole task graph as JSON: generous, still bounded.
_PLANNER_USER_TURN = "Decompose the goal above into a task graph, following the rules given."
_RETRIEVED_SEPARATOR = "\n\n"  # A blank line between hit blocks, as memory.assemble renders them.

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

    One value rather than a parameter list (codingrules 5.1's parameter limit), and the natural
    unit to hand a planner: the text as the human stated it, the data-sensitivity ceiling every
    subtask inherits, the Cells placement will match the plan's needs against, who asked for the
    goal in the first place, the Hive Stand's own scratch root (roadmap step 5.0b) so a declared
    leaving inside it is caught here, (roadmap step 5.0e) its own keep root, and (roadmap step
    7.9) what the Honey Store already knows about the goal, and (roadmap step 10.3) the capability
    set the goal's submitter holds, which every planned subtask inherits.
    """

    goal: str  # The goal text, as the human (or a bee on the human's behalf) stated it.
    clearance: HoneyClearance  # Every planned subtask's own clearance label (the goal's ceiling).
    cells: Sequence[Cell] | None = None  # The attached Wardens' Cells; None omits hot state.
    # Roadmap step 5.7a: every planned sub-task inherits the goal's own RequestOrigin, so a
    # Night Veil sub-task planned from a human goal still reads as human-originated at placement;
    # defaults HUMAN, matching every goal submitted through `hive run`/`hive tasks submit` today.
    origin: RequestOrigin = RequestOrigin.HUMAN
    # `hivemind.manifest`'s own `[hive_stand] scratch_root`, resolved; None when the caller has
    # none in hand (most unit tests), which simply skips PlannedTask's own scratch check.
    scratch_root: Path | None = None
    # `hivemind.manifest`'s own `[hive_stand] keep_root`, resolved; None when the operator has not
    # set one (or the caller has none in hand). `TaskAssign` carries no `keep_root` field of its
    # own -- adding one would be a wire change this step does not otherwise need (roadmap step
    # 5.0e: "the planner prompt instead tells the planner the keep root... via PlanBrief") -- so
    # this is the one place a Drone learns about it at all: `_build_request` folds it into hot
    # state, and the planner is expected to declare a `leaves` entry at (or under) this path when
    # the goal's own artefact belongs there, which then rides to the Drone unchanged on
    # `TaskAssign.leaves` (roadmap step 5.0b), exactly like any other declared leaving.
    keep_root: Path | None = None
    # Roadmap step 7.9: the Queen's Honey consultation for this goal (hivemind.queen.dispatcher.
    # consult_for_plan), each hit already scanned (screen_for_plan, roadmap step 10.6b); rendered
    # into the RETRIEVED section as reference data under its verdict. Empty (a Hive with no Honey
    # Store) renders no section at all.
    honey: tuple[RetrievedItem, ...] = ()
    # Roadmap step 10.3 (ADR-0039, "a goal carries a ceiling"): the submitter's capability set,
    # copied onto every planned subtask the way `origin` is, never read off the model's reply (a
    # planned task has no way to widen its own goal's set). None is the operator's own local
    # path (`hive run`), which has no device ceiling.
    capabilities: tuple[str, ...] | None = None
    # Roadmap step 10.5 (ADR-0040): the durable goal request this goal comes from. Its tier (when
    # it names one) overrides every planned task's own, since the human asked for it and a model
    # has no say over it; its id and budget are copied onto every task, never read off the reply.
    comb_shield: CombShieldLevel | None = None
    goal_request_id: GoalRequestId | None = None
    spend_cap_usd: float | None = None


async def plan_goal(
    brief: PlanBrief,
    bound: BoundModel,
    *,
    gate: CallGate,
    observer: LadderObserver | None = None,
) -> TaskGraphDraft:
    """Decompose `brief.goal` into a validated TaskGraphDraft, through `bound`.

    Args:
        brief: The goal, its clearance ceiling, the Cells the Hive can place work on (rendered
            by `describe_fleet` into hot state; None omits the section), the `RequestOrigin`
            every planned sub-task inherits, and its `scratch_root`, passed on as a validation
            context so `PlannedTask`'s own leaves-vs-scratch rule can fire inside the ladder
            below.
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
    request = _build_request(brief, bound)
    context: dict[str, Any] = {"scratch_root": brief.scratch_root}
    options = LadderOptions(observer=observer, context=context)
    # External await: one model call, latency class seconds to tens of seconds for a whole plan;
    # the ladder itself retries and steps down rungs on a malformed reply.
    result = await complete_structured(bound, request, PlanSchema, gate=gate, options=options)
    try:
        return _to_graph_draft(result.value, brief)
    except ValidationError as exc:
        raise PlannerError(f"The planned graph for {brief.goal[:80]!r} is invalid: {exc}") from exc


def _build_request(brief: PlanBrief, bound: BoundModel) -> LLMRequest:
    """Render `decompose_goal.md` and build the one request `plan_goal` sends to `bound`."""
    sections: dict[SectionLabel, str] = {SectionLabel.USER: brief.goal}
    hot_state_lines: list[str] = []
    if brief.cells is not None:
        # decompose_goal.md promises "a rough summary of the fleet's capacity" under hot state;
        # without it a model guesses (a Linux-only plan on a Windows Hive Stand never places).
        hot_state_lines.append(describe_fleet(brief.cells))
    if brief.keep_root is not None:
        # Roadmap step 5.0e: TaskAssign carries no keep_root field of its own (PlanBrief.keep_root's
        # own docstring), so this is the one place a plan ever learns it exists at all.
        hot_state_lines.append(
            f"Keep root: {brief.keep_root} -- declare a leaving at this path, or a location "
            "under it, for anything the goal itself needs to remain there."
        )
    if hot_state_lines:
        sections[SectionLabel.HOT_STATE] = "\n\n".join(hot_state_lines)
    retrieved = _retrieved_section(brief.honey, brief.clearance)
    if retrieved:
        # Roadmap step 7.9: what earlier work found, labelled reference data by the preamble and
        # delimited by render(); decompose_goal.md already names the section and its rules.
        sections[SectionLabel.RETRIEVED] = retrieved
    system = render(PromptName.DECOMPOSE_GOAL, sections=sections)
    return LLMRequest(
        slot=bound.slot,
        system=system,
        messages=(Message.text(Role.USER, _PLANNER_USER_TURN),),
        max_output_tokens=PLANNER_MAX_OUTPUT_TOKENS,
    )


def _retrieved_section(items: Sequence[RetrievedItem], clearance: HoneyClearance) -> str:
    """Render the goal's scanned Honey hits as the RETRIEVED section; empty when none may be shown.

    The Queen's consultation already capped the hits at the goal's clearance and packed them into
    her budget, so this renders every one it may show, best first, each under its scan verdict and
    cut at the same per-item cap hot state uses.
    """
    # Defence in depth: an item above the goal's own clearance, or one tainted since it was
    # retrieved, never reaches the planner's prompt.
    visible = [
        item
        for item in items
        if item.clearance.rank <= clearance.rank and not is_refused(item.tainted)
    ]
    if not visible:
        return ""  # No section at all rather than a preamble over nothing.
    visible.sort(key=lambda item: (-(item.hit.score if item.hit else 0.0), item.id))
    blocks = [render_item(item, ITEM_CAP_CHARS) for item in visible]
    return _RETRIEVED_SEPARATOR.join((RETRIEVED_PREAMBLE, *blocks))


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


def _to_graph_draft(plan: PlanSchema, brief: PlanBrief) -> TaskGraphDraft:
    """Convert every PlannedTask into a TaskDraft; TaskGraphDraft's own validators do the rest."""
    drafts = tuple(_to_task_draft(task, brief) for task in plan.tasks)
    return TaskGraphDraft(tasks=drafts)


def _to_task_draft(task: PlannedTask, brief: PlanBrief) -> TaskDraft:
    """Convert one PlannedTask, and every criterion it carries, into a TaskDraft.

    Every sub-task gets the same `origin` as the goal it was planned from (roadmap step 5.7a: "the
    planner's sub-tasks inherit the goal's origin"), never a value read off the model's own reply --
    a planned task has no way to assert who originally asked for the goal. The goal's capability
    set (roadmap step 10.3) and its request's id and budget (roadmap step 10.5) are inherited the
    same way, for the same reason; its clearance is the lower of the task's own and the goal's
    ceiling, and a tier the request named replaces the task's own (`_needs_at_tier`).
    """
    return TaskDraft(
        key=task.key,
        title=task.title,
        objective=task.objective,
        acceptance=tuple(_to_postcondition(item) for item in task.acceptance),
        role=task.role,
        needs=_needs_at_tier(task.needs, brief.comb_shield),
        clearance=min(task.clearance, brief.clearance, key=lambda label: label.rank),
        origin=brief.origin,
        depends_on=task.depends_on,
        leaves=task.leaves,
        capabilities=brief.capabilities,
        goal_request_id=brief.goal_request_id,
        spend_cap_usd=brief.spend_cap_usd,
    )


def _needs_at_tier(needs: TaskNeeds, tier: CombShieldLevel | None) -> TaskNeeds:
    """Return `needs` at the tier the human's request named; unchanged when it named none.

    Night Veil is virtual-only, so it also forces REQUIRED isolation (`TaskNeeds`' own rule);
    validated again rather than copied, so the pair can never come out inconsistent.
    """
    if tier is None:
        return needs  # No tier requested: the planner's own choice stands.
    isolation = Isolation.REQUIRED if tier is CombShieldLevel.NIGHT_VEIL else needs.isolation
    return TaskNeeds.model_validate(
        {**needs.model_dump(), "comb_shield": tier, "isolation": isolation}
    )


def _to_postcondition(item: PlannedPostcondition) -> Postcondition:
    """Convert one PlannedPostcondition into the real, cross-validated wire Postcondition."""
    return Postcondition(
        kind=item.kind, subject=item.subject, argv=item.argv, expected=item.expected
    )
