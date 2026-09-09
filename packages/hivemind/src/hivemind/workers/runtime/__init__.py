"""Provide the Worker runtime: the TickLoop that runs one Worker's whole life on the wire.

`WorkerRuntime` (roadmap step 3.15) is the standard long-running loop shape
(`waggle.loop.TickLoop`) specialised for one Worker: it receives `TaskAssign` and runs the role,
relays `TaskCancel`/`TaskPause`/`TaskResume`/every `Intervene` lever, sends `Heartbeat`/
`TaskProgress`/`TaskResult`, checkpoints and restarts the role at a handoff, and turns an
uncaught exception from the role into an `AlarmRaised` rather than a crash. `RuntimeDeps` is the
transport-side bundle it is built with. Internally the class is split across `loop` (the tick
loop and wire-message dispatch), `mailbox` (the transport, the receive/heartbeat race and the
blocking-question channel), `reporter` (this Worker's own WorkerState and every outgoing
message) and `attempt` (starting, cancelling and interpreting one role attempt), each under
codingrules 5.1's size limits; this face re-exports only what a caller outside this package
needs (codingrules 5.2).

Fits into the Hive:
    Layer 4 (roles that do the work). Constructed by `hivemind.wardens.spawn` (roadmap step
    3.19), one per sub-bee it starts. Calls into `hivemind.workers.base`, `.context`, `.errors`,
    `.state` and waggle.

Key invariants:
    - Whatever is not re-exported here (`Mailbox`, `Reporter`, `AttemptManager`, `WorkerRuntime`'s
      own private helpers) is private to this package (codingrules 5.4).

See Also:
    - .claude/codingrules.md section 11 for the TickLoop shape `WorkerRuntime` specialises.
    - .claude/roadmap.md phase 3 step 3.15 for the work that populates this package.
    - hivemind.workers.runtime.loop for WorkerRuntime itself.
    - hivemind.workers.runtime.deps for RuntimeDeps.

Public API:
    - RuntimeDeps: the transport, addressing, heartbeat cadence and clock a WorkerRuntime is
      built with (hivemind.workers.runtime.deps).
    - WorkerRuntime: the TickLoop that runs one Worker's whole life (hivemind.workers.runtime.loop).
"""

from hivemind.workers.runtime.deps import RuntimeDeps
from hivemind.workers.runtime.loop import WorkerRuntime

__all__ = ["RuntimeDeps", "WorkerRuntime"]
