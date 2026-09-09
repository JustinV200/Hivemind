"""Assemble a Drone's hot-state prompt and build the LLMRequest it sends to its bound model.

`assemble_drone_prompt` is a thin composition over `hivemind.memory.assemble`: it builds the
`Principal` (who this prompt is for), the `TriggerEvent` (the task assignment itself) and the
`TokenBudget` (a manifest-shaped fraction of the bound model's context window, minus an output
reserve) that `assemble` needs, then hands off to it -- never a conversation, per codingrules
section 8.8: "Awake episodes are stateless." `build_request` turns the resulting `Prompt` into the
`LLMRequest` `hivemind.llm.run_tool_loop` actually calls: the system prompt is
`hivemind.llm.prompts.drone_system.md` rendered with the assembled sections plus the triggering
event, and the one user turn is the task's own objective.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone`. Called by
    `hivemind.workers.roles.drone.Drone.run`. Calls into `hivemind.cell` (HoneyClearance),
    `hivemind.llm`, `hivemind.memory`, `hivemind.workers.context` and waggle only.

Key invariants:
    - `select_counter` returns a `hivemind.memory.ProviderCounter` only when the bound provider
      itself declares `token_counting`; every other binding gets a plain
      `hivemind.memory.EstimateCounter` (codingrules section 8.9: "provider count where available,
      else an estimate with margin").
    - The budget fraction and output reserve here are fixed constants, not read from a manifest:
      `hivemind.workers.context.WorkerContext` carries no manifest reference (codingrules section
      8.6's ladders already isolate `workers` from `hivemind.manifest`), so these mirror the
      manifest's own `[memory] budget_fraction`/`output_reserve_tokens` defaults rather than
      reading them.

See Also:
    - .claude/codingrules.md section 8.8 for "awake episodes are stateless."
    - .claude/codingrules.md section 8.9 for "stable prefix first" and the token-budget rule this
      module follows.
    - hivemind.memory.hot_state.packing for assemble, this module's one collaborator.
    - hivemind.llm.prompts for render and PromptName.DRONE_SYSTEM.
    - hivemind.workers.roles.drone for Drone, this module's one caller.
"""

from __future__ import annotations

from hivemind.cell import HoneyClearance
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
    Principal,
    Prompt,
    ProviderCounter,
    TokenBudget,
    TokenCounter,
    TriggerEvent,
    assemble,
)
from hivemind.workers.context import WorkerContext
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
    "build_request",
    "select_counter",
]


async def assemble_drone_prompt(
    ctx: WorkerContext, assignment: TaskAssign, sources: HotStateSources
) -> Prompt:
    """Assemble this attempt's hot-state prompt, budgeted against the bound model's window.

    Args:
        ctx: This attempt's WorkerContext; supplies `worker_id`, `bound` (for the slot and context
            window) and the counter selection.
        assignment: The task this attempt is working; becomes the Principal's clearance and the
            TriggerEvent's summary.
        sources: Where every hot-state candidate comes from (`hivemind.workers.roles.drone.
            sources.DroneSources` in production).

    Returns:
        A `hivemind.memory.Prompt`, packed to `DRONE_BUDGET_FRACTION` of `ctx.bound.context_window`
        minus `DRONE_OUTPUT_RESERVE_TOKENS`.
    """
    clearance = HoneyClearance.from_wire(assignment.clearance)
    principal = Principal(
        id=str(ctx.worker_id), slot=ctx.bound.slot, clearance=clearance, role=DRONE_ROLE
    )
    event = TriggerEvent(
        kind=TASK_ASSIGN_EVENT_KIND, summary=assignment.objective, clearance=clearance
    )
    budget = TokenBudget(
        max_input_tokens=int(ctx.bound.context_window * DRONE_BUDGET_FRACTION),
        output_reserve=DRONE_OUTPUT_RESERVE_TOKENS,
    )
    request = AssembleRequest(principal=principal, event=event, budget=budget)
    return await assemble(request, sources, select_counter(ctx))


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
        assignment: The task this attempt is working; its objective is the one user turn.
        tools: The tools this attempt's registry offers.

    Returns:
        A validated LLMRequest: `hivemind.llm.prompts.drone_system.md` rendered with `prompt`'s
        own sections plus the triggering event, one user turn of the task's objective, and
        `tools`.
    """
    system = render(
        PromptName.DRONE_SYSTEM, sections={**prompt.sections, SectionLabel.EVENT: prompt.event_text}
    )
    return LLMRequest(
        slot=ctx.bound.slot,
        system=system,
        messages=(Message.text(Role.USER, assignment.objective),),
        tools=tools,
        max_output_tokens=DRONE_OUTPUT_RESERVE_TOKENS,
    )


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
