"""Share one bounded, pausable, checkpointable tool loop across every role built on it.

Roadmap step 6.9: the Drone, the Forager and the Scout all do the same shape of work -- assemble a
prompt from durable state, hand a model a tool registry, loop tool calls to a stop, cooperate with
pause/cancel/handoff between calls -- and differ only in five small knobs
(`hivemind.workers.roles.bounded_loop.profile.RoleProfile`: which `WorkerRole` and system prompt,
how many rounds, which tools, and an optional per-tool-call hook). This package is that shared
machinery, factored out of `hivemind.workers.roles.drone` (its first owner) so the Forager and
Scout reuse it instead of copying it, while the Drone's own package keeps every one of its public
names, at their original paths, as thin wrappers or subclasses with no added behaviour -- its
tests, which import those names directly, run unchanged.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Used by `hivemind.workers.
    roles.drone.role.Drone`, `.forager.role.Forager` and `.scout.role.Scout`. Calls into
    `hivemind.llm`, `hivemind.memory`, `hivemind.workers.base`, `hivemind.workers.context`,
    `hivemind.workers.errors`, `hivemind.workers.telemetry`, `hivemind.workers.tools` and waggle.

Key invariants:
    - A role never marks its own work SUCCEEDED (codingrules section 8.7): `run_bounded_loop`
      only ever returns `claimed=True` to say the role believes the work is done.
    - Holds no conversation between attempts (codingrules section 8.8): a Handoff, when one is
      written, carries what the next attempt needs.

See Also:
    - .claude/codingrules.md section 5.2 for "a concept that needs a second file becomes a
      package", the reason this is a package rather than one large module.
    - .claude/roadmap.md step 6.9 for the Forager, and step 6.10 for the Scout.
    - hivemind.workers.roles.drone for the role this package's machinery was factored out of.

Public API:
    - RoleProfile, ToolCallResultHook, DEFAULT_BUDGET_FRACTION, DEFAULT_OUTPUT_RESERVE_TOKENS
      (profile): the five knobs one role's `role.py` sets.
    - RoleSources, ROLE_NOTES_LIMIT (sources): what one attempt knows, for hot-state packing.
    - HandoffRequestedError, LoopStoppedError, LoopExecutor, MAX_RECORDED_CALLS, collect_artifacts
      (executor): the tool executor every role's loop runs through.
    - build_claimed_outcome, build_handoff_outcome, MAX_SUMMARY_CHARS (outcome): the two ordinary
      ways one attempt ends.
    - assemble_role_prompt, build_request, brief_for, initial_budget, select_counter,
      TASK_ASSIGN_EVENT_KIND (prompt): assembling one attempt's request.
    - run_bounded_loop (runner): the one attempt shape every role's `run` delegates to.
"""

from hivemind.workers.roles.bounded_loop.executor import (
    MAX_RECORDED_CALLS,
    HandoffRequestedError,
    LoopExecutor,
    LoopStoppedError,
    collect_artifacts,
)
from hivemind.workers.roles.bounded_loop.outcome import (
    MAX_SUMMARY_CHARS,
    build_claimed_outcome,
    build_handoff_outcome,
)
from hivemind.workers.roles.bounded_loop.profile import (
    DEFAULT_BUDGET_FRACTION,
    DEFAULT_OUTPUT_RESERVE_TOKENS,
    RoleProfile,
    ToolCallResultHook,
)
from hivemind.workers.roles.bounded_loop.prompt import (
    TASK_ASSIGN_EVENT_KIND,
    assemble_role_prompt,
    brief_for,
    build_request,
    initial_budget,
    select_counter,
)
from hivemind.workers.roles.bounded_loop.runner import run_bounded_loop
from hivemind.workers.roles.bounded_loop.sources import ROLE_NOTES_LIMIT, RoleSources

__all__ = [
    "DEFAULT_BUDGET_FRACTION",
    "DEFAULT_OUTPUT_RESERVE_TOKENS",
    "MAX_RECORDED_CALLS",
    "MAX_SUMMARY_CHARS",
    "ROLE_NOTES_LIMIT",
    "TASK_ASSIGN_EVENT_KIND",
    "HandoffRequestedError",
    "LoopExecutor",
    "LoopStoppedError",
    "RoleProfile",
    "RoleSources",
    "ToolCallResultHook",
    "assemble_role_prompt",
    "brief_for",
    "build_claimed_outcome",
    "build_handoff_outcome",
    "build_request",
    "collect_artifacts",
    "initial_budget",
    "run_bounded_loop",
    "select_counter",
]
