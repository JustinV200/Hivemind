"""Define SubBee: one Warden's own bookkeeping record for one sub-bee it has spawned.

A `SubBee` is not a boundary value (it never crosses a network boundary itself, and it owns its own
mutable state in place, codingrules section 8.5): it is the Warden's own row for one Worker it is
supervising, mirroring that Worker's `hivemind.workers.state.WorkerState` from the last `Heartbeat`
or `TaskProgress` it reported, the binding key it last ran on (for a REBIND's "next binding in the
grant's allowed_bindings" search), its last `HandoffRef` (for a RETRY's `resume_from`), and its own
`hivemind.workers.runtime.WorkerRuntime` plus the `asyncio.Task` running it inside the Warden's own
`TaskGroup`. `missed_heartbeats` is the Warden's own watchdog counter, incremented once per
heartbeat interval that passes with nothing heard, reset the moment a fresh `Heartbeat` arrives;
crossing `WardenDeps.missed_heartbeats_before_stalled` is what turns into a synthesised
`AlarmKind.WORKER_STALLED`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Built
    by `hivemind.wardens.spawn.spawn.spawn_sub_bee`; read and mutated by
    `hivemind.wardens.warden.Warden` and its `hivemind.wardens.ticks` handlers on every report a
    sub-bee sends. Calls into `hivemind.workers` (WorkerState, WorkerRuntime) and waggle only.

Key invariants:
    - `state` only ever moves along `hivemind.workers.state.TRANSITIONS`; a SubBee's own state is
      a mirror of the runtime's real state, never a second source of truth the Warden invents.
    - `link` is the Warden's own end of the `waggle.transport.memory.MemoryTransport` pair; the
      Worker's own runtime holds the other end. Closed exactly once, when the sub-bee reaches a
      terminal state (or is killed) and the Warden is done with it, after `runtime_task` is
      stopped and reaped (`hivemind.wardens.spawn.spawn.stop_sub_bee`), never before.

See Also:
    - .claude/codingrules.md section 8.5 for the mutable-state-documented-here rule this class
      follows.
    - .claude/codingrules.md section 11 for the cooperative-stop-then-reap rule `stop_sub_bee`
      follows.
    - hivemind.workers.state for WorkerState, the mirrored machine this record's `state` follows.
    - hivemind.wardens.spawn.spawn for spawn_sub_bee and stop_sub_bee, this record's builder and
      its own stop path.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hivemind.workers.runtime import WorkerRuntime
from hivemind.workers.state import WorkerState
from waggle.ids import TaskId, WorkerId
from waggle.messages import HandoffRef
from waggle.messages.supervision import ContextTelemetry
from waggle.messages.task import TaskAssign
from waggle.transport.memory import MemoryTransport

__all__ = ["SubBee"]


@dataclass(slots=True)
class SubBee:
    """One sub-bee's bookkeeping row, owned and mutated in place by its Warden.

    Attributes:
        worker_id: This sub-bee's own id, the key the Warden's sub-bee table is keyed by.
        task_id: The task this sub-bee is (or was last) assigned.
        assignment: The TaskAssign this sub-bee is running; replaced whole on a respawn or rebind.
        attempt: The current attempt number, echoed on `assignment.attempt` and incremented by
            every RETRY/REBIND respawn.
        state: This sub-bee's own WorkerState, mirrored from its last Heartbeat/TaskProgress.
        last_telemetry: This sub-bee's own ContextTelemetry as of its last Heartbeat; None before
            its first one arrives. The source `Supervisor.inspect`'s CompactView is built from.
        binding: The `[llm.slots]` manifest key this sub-bee last ran on (a REBIND's starting
            point for "the next stronger binding" search).
        last_handoff: The last HandoffRef this sub-bee's runtime reported (a TaskProgress
            CHECKPOINTED stage), or None before its first checkpoint; a RETRY's `resume_from`.
        link: The Warden's own end of this sub-bee's transport pair.
        runtime: This sub-bee's own WorkerRuntime, so its owner can `stop()` it cooperatively
            (`hivemind.wardens.spawn.spawn.stop_sub_bee`) before falling back to cancelling
            `runtime_task`; never driven directly (its own `run()`, wrapped as `runtime_task`, is
            the one thing that ticks it).
        runtime_task: The asyncio.Task running `runtime`, owned by the Warden's own TaskGroup;
            awaited or cancelled by the Warden, never dropped.
        missed_heartbeats: How many heartbeat intervals have elapsed with nothing heard since the
            last Heartbeat; reset to 0 the moment a fresh one arrives.
    """

    worker_id: WorkerId
    task_id: TaskId
    assignment: TaskAssign
    attempt: int
    state: WorkerState
    binding: str
    last_handoff: HandoffRef | None
    link: MemoryTransport
    runtime: WorkerRuntime
    runtime_task: asyncio.Task[None]
    last_telemetry: ContextTelemetry | None = field(default=None)
    missed_heartbeats: int = field(default=0)
