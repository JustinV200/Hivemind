"""Assemble a Drone's hot-state prompt and build the LLMRequest it sends to its bound model.

`assemble_drone_prompt` is a thin composition over `hivemind.memory.assemble`: it builds the
`Principal` (who this prompt is for), the `TriggerEvent` (the task assignment itself) and the
`TokenBudget` (a manifest-shaped fraction of the bound model's context window, minus an output
reserve) that `assemble` needs, then hands off to it -- never a conversation, per codingrules
section 8.8: "Awake episodes are stateless." `build_request` turns the resulting `Prompt` into the
`LLMRequest` `hivemind.llm.run_tool_loop` actually calls: the system prompt is
`hivemind.llm.prompts.drone_system.md` rendered with the assembled sections plus the triggering
event, and the one user turn is the task's own objective. `brief_for` also renders
`TaskAssign.leaves` (roadmap step 5.0b) beside the acceptance criteria, unchanged from what the
plan declared -- a Drone reads what must stay but cannot widen the set, only raise a Question.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone`. Called by
    `hivemind.workers.roles.drone.Drone.run`. Calls into `hivemind.cell` (HoneyClearance),
    `hivemind.llm`, `hivemind.memory`, `hivemind.workers.context` and waggle only.

Key invariants:
    - `select_counter` returns a `hivemind.memory.ProviderCounter` only when the bound provider
      itself declares `token_counting`; every other binding gets a plain
      `hivemind.memory.EstimateCounter` (codingrules section 8.9: "provider count where available,
      else an estimate with margin").
    - The budget fraction and output reserve `initial_drone_budget` uses are fixed constants, not
      read from a manifest: `hivemind.workers.context.WorkerContext` carries no manifest reference
      (codingrules section 8.6's ladders already isolate `workers` from `hivemind.manifest`), so
      these mirror the manifest's own `[memory] budget_fraction`/`output_reserve_tokens` defaults
      rather than reading them.
    - `assemble_drone_prompt` takes its budget explicitly (roadmap step 4.4): `hivemind.workers.
      roles.drone.role.Drone.run` calls `initial_drone_budget` once, then re-calls
      `assemble_drone_prompt` with a smaller budget on each `hivemind.memory.overflow.
      run_with_overflow_retry` retry, so a `ContextTooLongError` never crashes a Drone.

See Also:
    - .claude/codingrules.md section 8.8 for "awake episodes are stateless."
    - .claude/codingrules.md section 8.9 for "stable prefix first" and the token-budget rule this
      module follows.
    - hivemind.memory.hot_state.packing for assemble, this module's one collaborator.
    - hivemind.llm.prompts for render and PromptName.DRONE_SYSTEM.
    - hivemind.workers.roles.drone for Drone, this module's one caller.
"""

from __future__ import annotations

from hivemind.cell import Cell, HoneyClearance
from hivemind.llm import (
    LLMRequest,
    Message,
    PromptName,
    Role,
    SectionLabel,
    ToolDefinition,
    render,
)
from hivemind.memory import (
    AssembleRequest,
    EstimateCounter,
    HotStateSources,
    MemoryContext,
    Principal,
    Prompt,
    ProviderCounter,
    Scorable,
    TokenBudget,
    TokenCounter,
    TriggerEvent,
    assemble,
    deposit_dropped_items,
)
from hivemind.workers.context import WorkerContext
from waggle.messages import PlannedLeaving, Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign

DRONE_ROLE = "drone"  # Principal.role for every Drone episode; matches the manifest key ("drone").
# Mirrors the manifest's [memory] budget_fraction/output_reserve_tokens defaults (docs/manifests):
# WorkerContext carries no manifest reference for a role to read these from directly (module
# docstring), so they are fixed here rather than threaded through from the composition root.
DRONE_BUDGET_FRACTION = 0.6
DRONE_OUTPUT_RESERVE_TOKENS = 4_096
TASK_ASSIGN_EVENT_KIND = "task.assign"  # TriggerEvent.kind for a fresh (non-resumed) attempt.

__all__ = [
    "DRONE_BUDGET_FRACTION",
    "DRONE_OUTPUT_RESERVE_TOKENS",
    "DRONE_ROLE",
    "TASK_ASSIGN_EVENT_KIND",
    "assemble_drone_prompt",
    "brief_for",
    "build_request",
    "initial_drone_budget",
    "select_counter",
]


def initial_drone_budget(ctx: WorkerContext) -> TokenBudget:
    """Return this attempt's starting TokenBudget, before any overflow ever shrinks it.

    Args:
        ctx: This attempt's WorkerContext; supplies `bound.context_window`.

    Returns:
        A `TokenBudget` at `DRONE_BUDGET_FRACTION` of `ctx.bound.context_window`, minus
        `DRONE_OUTPUT_RESERVE_TOKENS`.
    """
    return TokenBudget(
        max_input_tokens=int(ctx.bound.context_window * DRONE_BUDGET_FRACTION),
        output_reserve=DRONE_OUTPUT_RESERVE_TOKENS,
    )


async def assemble_drone_prompt(
    ctx: WorkerContext, assignment: TaskAssign, sources: HotStateSources, budget: TokenBudget
) -> Prompt:
    """Assemble this attempt's hot-state prompt at `budget`, depositing every dropped item.

    Args:
        ctx: This attempt's WorkerContext; supplies `worker_id`, `bound` (for the slot) and the
            counter selection.
        assignment: The task this attempt is working; becomes the Principal's clearance and the
            TriggerEvent's summary.
        sources: Where every hot-state candidate comes from (`hivemind.workers.roles.drone.
            sources.DroneSources` in production).
        budget: How much room the assembled sections have; `initial_drone_budget(ctx)` on the
            first attempt, a smaller one on each overflow retry (roadmap step 4.4).

    Returns:
        A `hivemind.memory.Prompt` packed to `budget`.
    """
    clearance = HoneyClearance.from_wire(assignment.clearance)
    principal = Principal(
        id=str(ctx.worker_id), slot=ctx.bound.slot, clearance=clearance, role=DRONE_ROLE
    )
    event = TriggerEvent(
        kind=TASK_ASSIGN_EVENT_KIND, summary=assignment.objective, clearance=clearance
    )
    request = AssembleRequest(principal=principal, event=event, budget=budget)
    dropped: list[Scorable] = []
    prompt = await assemble(request, sources, select_counter(ctx), on_drop=dropped.append)
    if dropped:
        # Roadmap step 4.4: "every dropped item is findable in Bee Bread by id."
        mem_ctx = MemoryContext(store=ctx.memory, identity=ctx.identity, clock=ctx.clock)
        await deposit_dropped_items(dropped, mem_ctx)
    return prompt


def build_request(
    ctx: WorkerContext,
    prompt: Prompt,
    assignment: TaskAssign,
    tools: tuple[ToolDefinition, ...],
) -> LLMRequest:
    """Build the LLMRequest a Drone's tool loop sends, from an assembled Prompt.

    Args:
        ctx: This attempt's WorkerContext; supplies the slot to call on.
        prompt: The assembled hot-state prompt.
        assignment: The task this attempt is working; its objective, acceptance criteria and
            the Cell's facts (`brief_for`) make up the one user turn.
        tools: The tools this attempt's registry offers.

    Returns:
        A validated LLMRequest: `hivemind.llm.prompts.drone_system.md` rendered with `prompt`'s
        own sections plus the triggering event, one user turn from `brief_for`, and `tools`.
    """
    system = render(
        PromptName.DRONE_SYSTEM, sections={**prompt.sections, SectionLabel.EVENT: prompt.event_text}
    )
    return LLMRequest(
        slot=ctx.bound.slot,
        system=system,
        messages=(Message.text(Role.USER, brief_for(assignment, ctx.cell)),),
        tools=tools,
        max_output_tokens=DRONE_OUTPUT_RESERVE_TOKENS,
    )


def brief_for(assignment: TaskAssign, cell: Cell) -> str:
    """Render the Drone's one user turn: the objective, what will be checked, and where it runs.

    The acceptance criteria are what the Warden will check, word for word, so the bee is told
    them rather than left to guess which file name or command the plan had in mind; the Cell's
    platform facts stop a bee on Windows proposing `python3` or a shell built-in.

    Args:
        assignment: The task being worked; its objective, acceptance criteria and leaves.
        cell: The Cell this attempt runs on; its capabilities name the OS, shell and Python.

    Returns:
        Plain text for the user turn, objective first.
    """
    # TaskAssign.acceptance is never empty on the wire (its own min_length), so there is always
    # at least one line to act on here.
    header = "Your Warden accepts this task only when every one of these holds:"
    lines = [assignment.objective, "", header]
    lines.extend(f"- {_describe_criterion(pc)}" for pc in assignment.acceptance)
    lines.extend(_leaves_lines(assignment.leaves))
    caps = cell.capabilities
    python = f"python {caps.python_version}" if caps.python_version else "no python"
    lines += [
        "",
        f"This Cell runs {caps.os.value} ({caps.arch}), shell {caps.shell}, {python}. A command is "
        "an argument list started without a shell: no shell built-ins, pipes or redirection. A "
        "relative path is relative to your working directory, which is also where the acceptance "
        "checks look.",
    ]
    return "\n".join(lines)


def _leaves_lines(leaves: tuple[PlannedLeaving, ...]) -> list[str]:
    """Render the plan's own declared leaves (roadmap step 5.0b), or nothing when there are none.

    A Drone cannot widen this set: every path outside scratch that is not listed here is removed
    on release regardless of anything the task does, so nothing else needs saying to enforce it.
    """
    if not leaves:
        return []
    lines = ["", "The plan asks these paths to remain once your task ends, and nothing else:"]
    lines.extend(f"- {leaving.pattern} ({leaving.reason})" for leaving in leaves)
    return lines


def _describe_criterion(pc: Postcondition) -> str:
    """Say one Postcondition in the words a bee acts on: the path or command, never the enum."""
    argv = " ".join(pc.argv)
    if pc.kind is PostconditionKind.FILE_EXISTS:
        return f"a file exists at {pc.subject}"
    if pc.kind is PostconditionKind.FILE_ABSENT:
        return f"no file exists at {pc.subject}"
    if pc.kind is PostconditionKind.COMMAND_EXITS_ZERO:
        return f"the command `{argv}` exits with code 0"
    if pc.kind is PostconditionKind.TEST_PASSES:
        return f"the test run `{argv}` passes"
    expected = f" -> {pc.expected}" if pc.expected else ""
    return f"{pc.kind.value}: {pc.subject}{expected}"


def select_counter(ctx: WorkerContext) -> TokenCounter:
    """Return a provider-backed counter when the bound provider counts tokens, else an estimate.

    Args:
        ctx: This attempt's WorkerContext; `ctx.bound.provider.capabilities.token_counting`
            decides which counter this returns (codingrules section 8.9).

    Returns:
        A `hivemind.memory.ProviderCounter` wrapping `ctx.bound.provider`, falling back to a
        `hivemind.memory.EstimateCounter`, when the provider declares `token_counting`; a bare
        `EstimateCounter` otherwise.
    """
    estimate = EstimateCounter()
    if ctx.bound.provider.capabilities.token_counting:
        return ProviderCounter(ctx.bound.provider, ctx.bound.slot, estimate)
    return estimate
