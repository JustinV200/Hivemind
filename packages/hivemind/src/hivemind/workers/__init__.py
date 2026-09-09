"""Provide the Worker runtime: the workers package.

A Worker is a subagent a Warden spawns to run one role (Forager, Scout, GuardBee, Undertaker,
Drone, HouseBee -- `waggle.messages.task.WorkerRole` names the six). Roadmap step 3.15 builds the
runtime every role shares: `base` fixes the one `Worker` Protocol every role implements and the
`WorkerOutcome` its `run` returns; `state` is the `WorkerState` machine and its one transition
table (codingrules Appendix C's "Worker" row); `context` is `WorkerContext`, everything a role may
use, plus `GrantSlice` (one Worker's share of a `ForageGrant`) and `QuestionChannel`; `telemetry`
is `TelemetryTracker`, the mutable per-Worker figures a role writes and the runtime reports;
`capabilities` computes a Worker's strict `CapabilitySet` slice from its Warden's; `errors` is this
subsystem's own error tree; `runtime` is `WorkerRuntime`, the `waggle.loop.TickLoop` that receives
`TaskAssign`, runs the role, reports back, and honours cancellation, pausing and every supervisor
intervention. It also holds the roles that do the Hive's actual work (`roles`, roadmap step 3.16),
the tools they call (`tools`), and the tactics they can invoke, such as writing in a more human
style under the Pheromone Mask (`tactics`).

Fits into the Hive:
    Layer 4 (roles that do the work). Called by `hivemind.wardens.spawn` (roadmap step 3.19),
    which builds a `hivemind.workers.context.WorkerContext` and a `hivemind.workers.runtime.
    RuntimeDeps` and starts a `hivemind.workers.runtime.WorkerRuntime` for each sub-bee it spawns.
    Calls into `hivemind.cell`, `hivemind.guard`, `hivemind.llm`, `hivemind.memory`,
    `hivemind.pheromone`, `hivemind.supervision` (never `hivemind.supervision.capping`) and
    waggle; never `hivemind.wardens` or `hivemind.queen` (codingrules section 4: "a Worker never
    imports its Warden").

Key invariants:
    - A Worker never marks itself SUCCEEDED: `WorkerOutcome.claimed` says the role believes the
      work is done; its Warden runs acceptance before that becomes final (roadmap step 3.18).
    - `WorkerState`'s member names and values mirror `waggle.messages.supervision.WorkerState`'s
      exactly (tests/unit/workers/test_state.py checks it member for member).
    - `hivemind.workers.capabilities.worker_capabilities` never returns a `CapabilitySet` wider
      than the Warden's own (codingrules section 15).
    - No module under this package imports `hivemind.wardens` or `hivemind.queen`.

See Also:
    - .claude/codingrules.md section 4 for the layer 4 row this package occupies.
    - .claude/codingrules.md Appendix C, "Worker" row, for the state machine `state` implements.
    - .claude/roadmap.md phase 3 step 3.15 for the work that populates this package's runtime;
      step 3.16 for `roles` and `tools`.
    - hivemind.workers.README for the module-by-module map of this package.

Public API (roadmap 3.15):
    - Worker, WorkerOutcome: the one role Protocol and what its `run` returns
      (hivemind.workers.base).
    - WorkerState, TRANSITIONS, assert_transition, can_transition, is_terminal: the Worker state
      machine (hivemind.workers.state).
    - GrantSlice, QuestionChannel, WorkerContext: everything a role may use
      (hivemind.workers.context).
    - TelemetryTracker: the mutable per-Worker telemetry a role writes (hivemind.workers.telemetry).
    - worker_capabilities: a Worker's strict CapabilitySet slice (hivemind.workers.capabilities).
    - WorkerError, InvalidWorkerTransitionError, WorkerCancelledError: this subsystem's error tree
      (hivemind.workers.errors).
    - RuntimeDeps, WorkerRuntime: the Worker runtime itself (hivemind.workers.runtime).
"""

from hivemind.workers.base import Worker, WorkerOutcome
from hivemind.workers.capabilities import worker_capabilities
from hivemind.workers.context import GrantSlice, QuestionChannel, WorkerContext
from hivemind.workers.errors import (
    InvalidWorkerTransitionError,
    WorkerCancelledError,
    WorkerError,
)
from hivemind.workers.runtime import RuntimeDeps, WorkerRuntime
from hivemind.workers.state import (
    TRANSITIONS,
    WorkerState,
    assert_transition,
    can_transition,
    is_terminal,
)
from hivemind.workers.telemetry import TelemetryTracker

__all__ = [
    "TRANSITIONS",
    "GrantSlice",
    "InvalidWorkerTransitionError",
    "QuestionChannel",
    "RuntimeDeps",
    "TelemetryTracker",
    "Worker",
    "WorkerCancelledError",
    "WorkerContext",
    "WorkerError",
    "WorkerOutcome",
    "WorkerRuntime",
    "WorkerState",
    "assert_transition",
    "can_transition",
    "is_terminal",
    "worker_capabilities",
]
