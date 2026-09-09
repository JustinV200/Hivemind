"""Implement Drone: the generic, disposable Worker role that runs a bounded tool loop.

A Drone is given one `TaskAssign`, assembles its own hot-state prompt (never a conversation,
codingrules section 8.8), and calls `hivemind.llm.run_tool_loop` with the tools its capabilities
allow (`hivemind.workers.tools.build_registry`) until the model stops calling tools, the round cap
is reached, or the attempt's own telemetry says to checkpoint. A Drone never marks itself SUCCEEDED
(codingrules section 8.7's task-acceptance counterpart): `WorkerOutcome.claimed=True` says only
that the role believes the work is done; its Warden (roadmap step 3.18) runs acceptance before that
becomes final. This package is split by responsibility (codingrules section 5.2): `sources.py` is
`DroneSources`, what one attempt knows for hot-state packing; `prompt.py` assembles that hot state
into a `Prompt` and builds the `LLMRequest`; `outcome.py` is `HandoffRequestedError` (the control
exception a checkpoint raises), the tool-executor adapter that cooperates with pause, cancel and
handoff between calls, and the two ways one attempt ends; `role.py` is `Drone` itself, the
role that ties the three together. This face only re-exports (codingrules section 5.4).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Constructed by a Warden's
    spawn logic (roadmap step 3.19) and run once per attempt by `hivemind.workers.runtime.
    WorkerRuntime`. Calls into `hivemind.llm`, `hivemind.memory`, `hivemind.workers.base`,
    `hivemind.workers.context`, `hivemind.workers.tools` and waggle only; never `hivemind.wardens`
    or `hivemind.queen` (codingrules section 4: "a Worker never imports its Warden").

Key invariants:
    - `Drone.run` catches exactly one exception by name, `HandoffRequestedError`; every other
      exception (a control exception such as `hivemind.workers.errors.WorkerCancelledError`
      included) propagates to `hivemind.workers.runtime.WorkerRuntime`, the one caller allowed to
      catch broadly (codingrules section 10).
    - `Drone.run` never calls `ctx.capping`, `ctx.session` or `ctx.asker` directly: every tool call
      goes through the registry `hivemind.workers.tools.build_registry` built, so a role's own code
      never re-implements what a tool already owns.
    - `run_tool_loop` exposes no per-round telemetry hook; this attempt's tokens and spend are
      recorded on `ctx.telemetry` exactly once, from the loop's own summed `Usage`, after it
      returns (flagged as a deviation from the roadmap's per-round phrasing in this step's report).

See Also:
    - .claude/codingrules.md section 8.7 for "a Worker never marks itself SUCCEEDED."
    - .claude/codingrules.md section 8.8 for "awake episodes are stateless."
    - .claude/codingrules.md section 8.9 for the handoff-threshold rule `HandoffRequestedError`
      answers.
    - .claude/roadmap.md phase 3 step 3.16 for the bullet this package implements.
    - hivemind.workers.roles.drone.outcome for HandoffRequestedError and the two outcome builders.
    - hivemind.workers.tools for build_registry, the tool set this role calls through.

Public API (roadmap 3.16):
    - Drone, DRONE_MAX_ROUNDS: the role itself, and its default tool-loop round cap.
    - HandoffRequestedError: the control exception a checkpoint raises between tool calls
      (hivemind.workers.roles.drone.outcome).
"""

from hivemind.workers.roles.drone.outcome import HandoffRequestedError
from hivemind.workers.roles.drone.role import DRONE_MAX_ROUNDS, Drone

__all__ = ["DRONE_MAX_ROUNDS", "Drone", "HandoffRequestedError"]
