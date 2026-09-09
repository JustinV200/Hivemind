"""Define Reporter: WorkerRuntime's own delegate for its WorkerState and every outgoing message.

`Reporter` is split out of `hivemind.workers.runtime.loop.WorkerRuntime` only for size
(codingrules section 5.2), the same way `hivemind.workers.runtime.attempt.AttemptManager` is: it
owns the two pieces of state every state change and every outgoing message reads --
`hivemind.workers.state.WorkerState` and the active `TaskAssign` -- plus the one call each report
kind needs (`hivemind.workers.runtime.reports.build_heartbeat`/`build_progress`/`build_result`/
`build_alarm`) and the `hivemind.pheromone.WorkerEvent` every state change writes. It is
`WorkerRuntime`'s own delegate, not a general-purpose class, and is used the same way by
`hivemind.workers.runtime.attempt.AttemptManager` (through `WorkerRuntime._reporter`) so both
classes report through the exact same state and the exact same trail events.

Fits into the Hive:
    Layer 4 (roles that do the work). Owned by exactly one `WorkerRuntime` instance (roadmap step
    3.15), constructed in its `__init__`. Calls into `hivemind.common.errors`, `hivemind.pheromone`
    and this package's own `mailbox`/`reports`, plus waggle.

Key invariants:
    - `transition` is the only place `_state` changes; every caller goes through it, so a Worker's
      state only ever moves along `hivemind.workers.state.TRANSITIONS`' edges.
    - `require_assignment` is the one place a missing TaskAssign becomes a typed
      `InvariantViolationError` rather than an `AttributeError` on `None`.

See Also:
    - .claude/codingrules.md section 5.2 for the module-split rule this class follows.
    - hivemind.workers.state for WorkerState and its transition table.
    - hivemind.workers.runtime.loop for WorkerRuntime, this class's one owner.
    - hivemind.workers.runtime.attempt for AttemptManager, this class's other caller.
    - hivemind.workers.runtime.reports for the pure builders this class's `send_*` methods wrap.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.common.errors import InvariantViolationError
from hivemind.memory import MemoryContext
from hivemind.pheromone import WorkerEvent
from hivemind.workers.context import WorkerContext
from hivemind.workers.runtime.deps import RuntimeDeps
from hivemind.workers.runtime.mailbox import Mailbox
from hivemind.workers.runtime.reports import (
    AlarmDetails,
    ResultDetails,
    build_alarm,
    build_heartbeat,
    build_progress,
    build_result,
)
from hivemind.workers.state import WorkerState, assert_transition
from waggle.ids import new_event_id
from waggle.messages import HandoffRef
from waggle.messages.task import TaskAssign, TaskStage

__all__ = ["Reporter"]


class Reporter:
    """Own one Worker's WorkerState and active TaskAssign; build and send every report.

    Owns its own mutable state in place (codingrules section 8.5): `_state` and `_assignment`
    change as the Worker this reports for runs.
    """

    def __init__(self, ctx: WorkerContext, deps: RuntimeDeps, mailbox: Mailbox) -> None:
        """Build a Reporter in WorkerState.SPAWNED, with no active TaskAssign.

        Args:
            ctx: This Worker's context; supplies the identity and trail every report stamps.
            deps: This Worker's transport-side collaborators; supplies the clock and heartbeat
                cadence.
            mailbox: Where every built message is sent.
        """
        self._ctx = ctx
        self._deps = deps
        self._mailbox = mailbox
        self._state = WorkerState.SPAWNED
        self._assignment: TaskAssign | None = None

    @property
    def state(self) -> WorkerState:
        """This Worker's current WorkerState."""
        return self._state

    @property
    def assignment(self) -> TaskAssign | None:
        """The active TaskAssign, or None before the first one arrives."""
        return self._assignment

    def set_assignment(self, assign: TaskAssign) -> None:
        """Record `assign` as the active TaskAssign every later report echoes."""
        self._assignment = assign

    def require_assignment(self) -> TaskAssign:
        """Return the active TaskAssign, or raise if this Worker has none.

        Raises:
            hivemind.common.errors.InvariantViolationError: No TaskAssign has arrived yet; every
                caller of this method only runs once one has (a role only runs after TaskAssign).
        """
        if self._assignment is None:
            raise InvariantViolationError(
                "WorkerRuntime has no active TaskAssign to report against."
            )
        return self._assignment

    def memory_context(self) -> MemoryContext:
        """Build the MemoryContext a checkpoint write needs from this Worker's own collaborators."""
        return MemoryContext(
            store=self._ctx.memory, identity=self._ctx.identity, clock=self._deps.clock
        )

    def transition(self, to_state: WorkerState) -> None:
        """Move this Worker's state to `to_state`, or raise if the edge is not legal."""
        assert_transition(self._state, to_state, worker_id=self._ctx.worker_id)
        self._state = to_state

    async def record_event(self, kind: str, **payload: JsonValue) -> None:
        """Build and record one `worker.*` WorkerEvent, subject to this Worker's own id."""
        event = WorkerEvent(
            id=new_event_id(self._deps.clock),
            hive_id=self._ctx.identity.hive_id,
            node_id=self._ctx.identity.node_id,
            at=self._deps.clock.now(),
            actor=self._ctx.identity.actor,
            kind=kind,
            subject_id=self._ctx.worker_id,
            payload=payload,
        )
        await self._ctx.trail.record(event)

    async def send_heartbeat(self) -> None:
        """Send this Worker's own Heartbeat: state, telemetry, grant spend, current task."""
        task_id = self._assignment.task_id if self._assignment is not None else None
        message = build_heartbeat(self._ctx, self._state, task_id, self._deps.heartbeat_interval_s)
        await self._mailbox.send(message)

    async def send_progress(
        self, stage: TaskStage, summary: str, handoff: HandoffRef | None = None
    ) -> None:
        """Send a TaskProgress reporting `stage`."""
        message = build_progress(self.require_assignment(), stage, summary, handoff)
        await self._mailbox.send(message)

    async def send_result(self, details: ResultDetails) -> None:
        """Send a TaskResult closing this attempt."""
        message = build_result(self.require_assignment(), details)
        await self._mailbox.send(message)

    async def send_alarm(self, details: AlarmDetails) -> None:
        """Send an AlarmRaised escalating an issue this Worker cannot resolve itself."""
        message = build_alarm(self._ctx, self.require_assignment(), details)
        await self._mailbox.send(message)
