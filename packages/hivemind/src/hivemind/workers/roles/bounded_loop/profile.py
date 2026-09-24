"""Define RoleProfile: the small, per-role knob set the bounded-loop runner reads.

A Worker role built on the bounded tool loop (the Drone, the Forager, roadmap step 6.9, and the
Scout, roadmap step 6.10) differs from its siblings in exactly five ways: which `waggle.messages.
task.WorkerRole` it reports, what its `Principal.role` string is for hot-state packing, which
system prompt it opens with, how many rounds its loop may take, how it builds its own tool
registry, and (roadmap step 6.9) an optional hook run after every successful tool call, so a role
such as the Forager can act on a result (deposit it as Nectar) without the shared loop knowing
anything about Bee Bread. Bundling these five knobs in one frozen value, rather than five
parameters, keeps `hivemind.workers.roles.bounded_loop.runner.run_bounded_loop` under codingrules
5.1's parameter limit and keeps every role's own `role.py` a two-line body: build a `RoleProfile`,
hand it to the shared runner.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.bounded_loop`. Built once per
    attempt by each role's own `role.py` (`hivemind.workers.roles.drone.role.Drone`, `.forager.
    role.Forager`, `.scout.role.Scout`); read by `hivemind.workers.roles.bounded_loop.runner.
    run_bounded_loop`, the one function every role's `run` delegates to. Calls into `hivemind.llm`
    (PromptName), `hivemind.workers.context` (WorkerContext), `hivemind.workers.tools` (ToolCall,
    ToolOutput, ToolRegistry) and waggle only.

Key invariants:
    - `max_rounds` is always at least 1: `hivemind.llm.run_tool_loop` itself raises otherwise, so a
      role that built a profile with a smaller cap would only find out at its very first call.
    - `on_tool_result`, when set, is called only after a tool call that did not fail (the runner's
      own contract, not this module's): a role's hook never has to re-check `is_error` itself.

See Also:
    - .claude/codingrules.md section 5.1 for "introduce a frozen dataclass for the argument group",
      the reason this bundle exists at all.
    - hivemind.workers.roles.bounded_loop.runner for run_bounded_loop, this profile's one reader.
    - hivemind.workers.roles.forager.nectar for the one `on_tool_result` hook shipped so far.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from hivemind.llm import ToolCall
from hivemind.llm.prompts import PromptName
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools import ToolOutput, ToolRegistry
from waggle.messages.task import TaskAssign, WorkerRole

# Shared by every role built on the bounded loop, unless a role has a documented reason to differ
# (mirrors the manifest's own [memory] budget_fraction/output_reserve_tokens defaults, the same way
# hivemind.workers.roles.drone.prompt's own constants always have -- WorkerContext carries no
# manifest reference for a role to read these from directly, codingrules section 8.6's ladders).
DEFAULT_BUDGET_FRACTION = 0.6
DEFAULT_OUTPUT_RESERVE_TOKENS = 4_096

# The one hook a profile may carry: context, active assignment, the model's own call, its result.
ToolCallResultHook = Callable[[WorkerContext, TaskAssign, ToolCall, ToolOutput], Awaitable[None]]

__all__ = [
    "DEFAULT_BUDGET_FRACTION",
    "DEFAULT_OUTPUT_RESERVE_TOKENS",
    "RoleProfile",
    "ToolCallResultHook",
]


@dataclass(frozen=True, slots=True)
class RoleProfile:
    """The five knobs that make one bounded-loop role different from its siblings.

    Attributes:
        role: The `WorkerRole` this profile's owner reports as its own `Worker.role`.
        principal_role: The short, lowercase string `hivemind.memory.hot_state.summaries.
            Principal.role` carries for this role's episodes (mirrors `DRONE_ROLE`'s own shape).
        prompt_name: Which shipped system prompt this role's requests open with.
        max_rounds: The most model turns one attempt's tool loop may take; see the module
            docstring's own invariant.
        budget_fraction: The fraction of `ctx.bound.context_window` this role's starting
            `hivemind.memory.TokenBudget` gets, before any overflow ever shrinks it.
        output_reserve_tokens: Held back from the budget for the model's own reply, and passed as
            `LLMRequest.max_output_tokens`.
        build_tools: Builds this role's own `ToolRegistry` from its `WorkerContext`; usually
            `hivemind.workers.tools.build_registry` itself, or a role's own narrower builder (the
            Scout's `hivemind.workers.roles.scout.tools.scout_build_registry`).
        on_tool_result: Called after every tool call that did not fail, with the context, the
            active assignment, the model's own `ToolCall` and the tool's `ToolOutput`; `None` (the
            default) runs nothing extra. The Forager's Nectar deposit is the one user so far.
    """

    role: WorkerRole
    principal_role: str
    prompt_name: PromptName
    max_rounds: int
    build_tools: Callable[[WorkerContext], ToolRegistry]
    budget_fraction: float = DEFAULT_BUDGET_FRACTION
    output_reserve_tokens: int = DEFAULT_OUTPUT_RESERVE_TOKENS
    on_tool_result: ToolCallResultHook | None = None
