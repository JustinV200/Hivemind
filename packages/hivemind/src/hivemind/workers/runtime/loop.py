"""Define WorkerRuntime: the TickLoop that runs one Worker's whole life on the wire.

`WorkerRuntime` is the standard long-running loop shape (`waggle.loop.TickLoop`, codingrules
section 11) specialised for one Worker: receive `TaskAssign` and start the role
(`hivemind.workers.base.Worker.run`); relay `TaskCancel`, `TaskPause`, `TaskResume` and every
`Intervene` lever to it; send `Heartbeat`, `TaskProgress` and `TaskResult`; checkpoint and restart
the role at a handoff; and turn an uncaught exception from the role into an `AlarmRaised` rather
than a crash. Every state change goes through `hivemind.workers.state.assert_transition` and
writes its own `worker.*` trail event in the same call (codingrules Appendix C's "Worker" row and
section 12's "a state change and its trail event share one transaction"). One tick handles exactly
one thing: the next envelope from `hivemind.workers.runtime.mailbox.Mailbox`, the next heartbeat
deadline, the role's own task finishing, a scheduled cancel deadline firing, or `stop()` having
been called -- whichever comes first (`asyncio.wait(..., return_when=FIRST_COMPLETED)`), so a role
blocked asking a Question never stops heartbeats or `TaskCancel` from being noticed. Every tick
also drains `hivemind.workers.telemetry.TelemetryTracker.take_pending_alarms` (this dispatch's own
fix 2: a Capping rollback several awaits deep inside the role's own tool loop has no handle on
this runtime, so it notes the Alarm on the shared tracker instead and this is where it actually
gets sent). Draining only at the top of a tick was not enough on its own: when the role's own
natural completion (claimed, or a terminal handoff) woke the same tick that noted a pending Alarm,
the tick moved straight to a terminal WorkerState with the Alarm still queued, and nothing drained
it again afterwards. `hivemind.workers.runtime.attempt.AttemptManager` now flushes the queue
before every terminal transition (DONE/FAILED/KILLED) too, and `_tick` flushes once more right
before `run()` is about to return (the `stop()` branch below and `_on_transport_closed`), in the
order the Alarms were noted; a transport already closed by the time a flushed Alarm tries to send
is recoverable (`_send_pending_alarms` logs it and keeps going), never an exception out of this
loop. This class's own state and every outgoing message go through `hivemind.workers.
runtime.reporter.Reporter`, and starting the role, scheduling its cancel and interpreting its
finished task go through `hivemind.workers.runtime.attempt.AttemptManager`, both split out only to
keep this class inside codingrules 5.1's size limits.

Fits into the Hive:
    Layer 4 (roles that do the work). Constructed by `hivemind.wardens.spawn` (roadmap step 3.19)
    once per sub-bee it starts, inside a `TaskGroup` it owns. Calls into `hivemind.memory`
    (`read_handoff`, `Handoff`), `hivemind.cell` (`HoneyClearance`), `hivemind.supervision` (the
    Intervention levers only, never `hivemind.supervision.capping`), `hivemind.workers.base`,
    `.context`, `.errors`, `.state`, this package's own `attempt`, `deps`, `mailbox` and
    `reporter`, and waggle.

Key invariants:
    - Every WorkerState change and every outgoing message goes through `_reporter`
      (`hivemind.workers.runtime.reporter.Reporter`); `hivemind.workers.runtime.attempt.
      AttemptManager` reports through the very same instance, so the two classes never disagree
      about this Worker's own state.
    - `_dispatch` catches `InvalidWorkerTransitionError` around each handler, so one out-of-order
      or duplicate wire message is logged and dropped rather than ending `run()`.
    - `stop()` (TickLoop's own) always ends with `Mailbox.aclose()` having run exactly once,
      whether `stop()` was called by this runtime's owner or the transport ended on its own.
    - Every pending Alarm noted before a terminal WorkerState transition, or before `run()` is
      about to return, has been flushed by the time that transition or return happens (this
      dispatch's own fix 2): `_send_pending_alarms` is the one function every flush call site
      shares, so a rollback Alarm is never left queued once its attempt is over.
    - `_tick`'s own throwaway `stop_task` is reaped via `hivemind.common.tasks.reaping`, wrapped
      around the `asyncio.wait` that races it, so a tick cancelled from outside (this runtime's
      own `run()` task, cancelled by its owner without going through cooperative `stop()` first)
      never abandons it destroyed pending (this dispatch's own shutdown-hygiene fix, codingrules
      section 11).

See Also:
    - .claude/codingrules.md section 11 for the TickLoop shape this class specialises.
    - .claude/codingrules.md Appendix C, "Worker" row, for the state machine this class drives.
    - .claude/codingrules.md section 5.2 for the module-split rule behind `Reporter` and
      `AttemptManager`.
    - hivemind.workers.state for WorkerState and its transition table.
    - hivemind.workers.runtime.mailbox for Mailbox, this class's one link to its Warden.
    - hivemind.workers.runtime.reporter for Reporter, this class's own state-and-report delegate.
    - hivemind.workers.runtime.attempt for AttemptManager, this class's own attempt delegate.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from hivemind.cell import HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reaping
from hivemind.memory import Handoff, read_handoff
from hivemind.supervision.intervention import Checkpoint, Compact, Rebind, Takeover
from hivemind.supervision.intervention import Handoff as HandoffLever
from hivemind.supervision.intervention import from_wire as intervention_from_wire
from hivemind.workers.base import Worker
from hivemind.workers.context import WorkerContext
from hivemind.workers.errors import InvalidWorkerTransitionError
from hivemind.workers.runtime.attempt import AttemptManager
from hivemind.workers.runtime.deps import RuntimeDeps
from hivemind.workers.runtime.mailbox import Mailbox
from hivemind.workers.runtime.reporter import Reporter
from hivemind.workers.runtime.reports import AlarmDetails
from hivemind.workers.state import WorkerState, can_transition, is_terminal
from waggle.envelope import Envelope
from waggle.errors import TransportClosedError
from waggle.loop import TickLoop
from waggle.messages.supervision import Answer, Intervene
from waggle.messages.task import TaskAssign, TaskCancel, TaskPause, TaskResume, TaskStage

log = get_logger(__name__)

# An Intervene carries no grace period of its own (unlike TaskCancel); the supervisor pulling this
# lever has already decided cancellation is warranted, so it takes effect at once.
DEFAULT_INTERVENE_CANCEL_GRACE_S = 0.0

__all__ = ["WorkerRuntime"]


class WorkerRuntime(TickLoop):
    """Run one Worker's whole life: assign, report, checkpoint, cancel, intervene, heartbeat.

    Its own state lives on `_reporter` and the running attempt's own task handles on `_attempt`
    (codingrules section 8.5's "a class that owns mutable state documents it" applies to those two
    delegates, not to this class directly). `run()` and `stop()` are inherited from `TickLoop`
    unchanged; only `_tick` and its own helpers are new here.
    """

    def __init__(self, ctx: WorkerContext, worker: Worker, deps: RuntimeDeps) -> None:
        """Build a WorkerRuntime in WorkerState.SPAWNED, ready for its first TaskAssign.

        Args:
            ctx: Everything the role may use; this constructor replaces `ctx.asker` with this
                runtime's own mailbox (`hivemind.workers.context`'s module docstring explains why
                the caller cannot wire that in beforehand).
            worker: The role implementation to run once assigned.
            deps: The transport, addressing, heartbeat cadence and clock this runtime is built
                with.
        """
        super().__init__(deps.clock)
        self._worker = worker
        self._deps = deps
        self._mailbox = Mailbox(deps.transport, deps.hop, deps.clock, deps.heartbeat_interval_s)
        # WorkerContext is frozen (codingrules 8.5); a fresh copy carries the real QuestionChannel.
        self._ctx = dataclasses.replace(ctx, asker=self._mailbox)
        self._reporter = Reporter(self._ctx, deps, self._mailbox)
        self._attempt = AttemptManager(self)

    @property
    def state(self) -> WorkerState:
        """This Worker's current WorkerState."""
        return self._reporter.state

    # ──────────────────────────────────────────────────────────────────────────
    # The tick: race the next envelope, the heartbeat deadline, the attempt's own
    # role task and cancel deadline, and stop() -- handle exactly one per call.
    # ──────────────────────────────────────────────────────────────────────────

    async def _tick(self) -> None:
        """Wait for whichever of this Worker's wake sources fires first, and handle only that."""
        # Checked every tick, whatever wakes it: a Capping rollback noted on this attempt's own
        # telemetry (hivemind.workers.tools.proposals.cap, this dispatch's own fix 2) is never
        # more than one tick's delay from becoming a real AlarmRaised to this Worker's Warden.
        await self._drain_pending_alarms()
        receive_task = self._mailbox.receive_task()
        heartbeat_task = self._mailbox.heartbeat_task()
        # Throwaway: only wakes this asyncio.wait early when stop() is called mid-tick; cancelled
        # below if some other source won the race, so it is never a dropped, unowned task.
        stop_task: asyncio.Task[bool] = asyncio.ensure_future(self._stop.wait())
        # asyncio.Future is invariant in its type parameter, so a set typed over each Task's own
        # result type cannot hold Tasks of different result types together; Any is the escape
        # hatch asyncio.wait's own signature expects for a heterogeneous waitable set.
        waitables: set[asyncio.Future[Any]] = {receive_task, heartbeat_task, stop_task}
        role_task = self._attempt.role_task
        if role_task is not None:
            waitables.add(role_task)
        cancel_deadline_task = self._attempt.cancel_deadline_task
        if cancel_deadline_task is not None:
            waitables.add(cancel_deadline_task)
        # reaping: stop_task must never be left pending even if this tick is cancelled from outside.
        async with reaping(stop_task):
            done, _pending = await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
        if stop_task in done:
            # Fix 2: flush any Alarm still queued rather than silently dropping it with the
            # runtime; then reap a role still mid-attempt, never left running with no owner.
            await self._drain_pending_alarms()
            await self._attempt.cancel_role_task()
            await self._mailbox.aclose()
            return
        if role_task is not None and role_task in done:
            await self._attempt.on_finished()
            return
        if cancel_deadline_task is not None and cancel_deadline_task in done:
            self._attempt.force_cancel_if_due()
            return
        if receive_task in done:
            envelope = self._mailbox.take_received()
            if envelope is None:
                await self._on_transport_closed()
            else:
                await self._dispatch(envelope)
            return
        if heartbeat_task in done:
            self._mailbox.clear_heartbeat()
            await self._reporter.send_heartbeat()

    async def _drain_pending_alarms(self) -> None:
        """Send every Alarm a tool-level failure noted on this attempt's own telemetry tracker.

        Module-level, not inline, so this class stays within codingrules 5.1's size limit; see
        `_send_pending_alarms` below for what it actually does and why.
        """
        await _send_pending_alarms(self)

    async def _dispatch(self, envelope: Envelope) -> None:
        """Route one received envelope's payload to its handler, dropping an illegal transition."""
        payload = envelope.payload
        try:
            if isinstance(payload, TaskAssign):
                await self._handle_assign(payload)
            elif isinstance(payload, TaskCancel):
                self._attempt.request_cancel(payload.reason, payload.grace_s)
            elif isinstance(payload, TaskPause):
                await self._handle_pause(payload)
            elif isinstance(payload, TaskResume):
                await self._handle_resume(payload)
            elif isinstance(payload, Intervene):
                await self._handle_intervene(payload)
            elif isinstance(payload, Answer):
                self._mailbox.resolve_answer(payload)
            # Anything else is not addressed to a Worker's mailbox; ignored so a peer's unrelated
            # message can never take this runtime down.
        except InvalidWorkerTransitionError as error:
            log.warning(
                "workers.runtime.invalid_transition",
                worker_id=self._ctx.worker_id,
                error=str(error),
            )

    async def _on_transport_closed(self) -> None:
        """React to the link to this Worker's Warden ending, cleanly or otherwise.

        Policy: the transport ending -- a clean close or a dropped link alike -- always ends this
        runtime. A `MemoryTransport` pair cannot reconnect (a fresh pair is needed to talk again),
        so there is nothing to recover into; if this Worker was mid-attempt, it is recorded KILLED
        on the trail (which needs no Warden link, since the trail is written directly, not over
        the wire) before `stop()` ends `run()`. No TaskResult is sent: the transport that would
        carry it is already gone, and a Worker's own hop may only ever claim regardless (see
        `hivemind.workers.runtime.attempt.AttemptManager`'s own module docstring).
        """
        log.warning(
            "workers.runtime.transport_closed",
            worker_id=self._ctx.worker_id,
            state=self.state.value,
        )
        if not is_terminal(self.state) and can_transition(self.state, WorkerState.KILLED):
            # This dispatch's own fix 2: flush before the terminal transition, even though the
            # transport that just ended is exactly what makes the wire send itself recoverable
            # (_send_pending_alarms) -- the alarm.raised trail event still needs to land.
            await self._drain_pending_alarms()
            self._reporter.transition(WorkerState.KILLED)
            await self._reporter.record_event(
                "worker.killed", cancel_reason="The link to this Worker's Warden ended."
            )
        await self._mailbox.aclose()
        self.stop()

    # ──────────────────────────────────────────────────────────────────────────
    # TaskAssign, TaskPause, TaskResume, Intervene.
    # ──────────────────────────────────────────────────────────────────────────

    async def _handle_assign(self, assign: TaskAssign) -> None:
        """Move SPAWNED -> RUNNING, resolve any resume_from, and start the role."""
        self._reporter.transition(WorkerState.RUNNING)
        self._reporter.set_assignment(assign)
        await self._reporter.record_event("worker.started", task_id=assign.task_id)
        resume_from: Handoff | None = None
        if assign.resume_from is not None:
            allowance = HoneyClearance.from_wire(assign.clearance)
            resume_from = await read_handoff(self._ctx.memory, assign.resume_from, allowance)
        await self._reporter.send_progress(TaskStage.STARTED, "Started.")
        self._attempt.start(resume_from)

    async def _handle_pause(self, pause: TaskPause) -> None:
        """Move RUNNING -> PAUSED and hold the role at its own next `wait_if_paused()` call."""
        self._reporter.transition(WorkerState.PAUSED)
        self._ctx.telemetry.pause()
        await self._reporter.record_event("worker.paused")
        await self._reporter.send_progress(TaskStage.PAUSED, f"Paused: {pause.reason}")

    async def _handle_resume(self, resume: TaskResume) -> None:
        """Move PAUSED -> RUNNING and release the role from its own pause wait."""
        self._reporter.transition(WorkerState.RUNNING)
        self._ctx.telemetry.resume()
        await self._reporter.record_event("worker.resumed")
        await self._reporter.send_progress(TaskStage.RESUMED, f"Resumed: {resume.reason}")

    async def _handle_intervene(self, intervene: Intervene) -> None:
        """Pull one supervisor lever on this Worker.

        Compact and Checkpoint both ask for a handoff at the role's own next opportunity and
        continue afterwards; Handoff, Rebind and Takeover ask for the same handoff but stop this
        attempt once it is written; Cancel behaves exactly like TaskCancel, except an Intervene
        carries no grace period of its own.
        """
        lever = intervention_from_wire(intervene)
        # An isinstance chain over the lever's own discriminated union, not a `.kind` match on a
        # Cell (scripts/check_no_kind_branches.py's actual target): the same pattern
        # hivemind.supervision.intervention.to_wire itself uses, for the same reason.
        if isinstance(lever, Compact | Checkpoint):
            self._attempt.request_handoff(stop_after=False)
            return
        if isinstance(lever, HandoffLever | Rebind | Takeover):
            reason = f"{type(lever).__name__} intervention: {lever.reason}"
            self._attempt.request_handoff(stop_after=True, reason=reason)
            return
        # The only remaining variant is Cancel; every Intervention variant carries `reason`, so
        # no narrowing is needed to reach it here.
        self._attempt.request_cancel(lever.reason, DEFAULT_INTERVENE_CANCEL_GRACE_S)


async def _send_pending_alarms(runtime: WorkerRuntime) -> None:
    """Send every Alarm a tool-level failure noted on this attempt's own telemetry tracker.

    A tool's own call into Capping (`hivemind.workers.tools.proposals.cap`) runs several awaits
    deep inside the role's own tool loop, with no handle on this runtime; noting the Alarm on
    `ctx.telemetry` (this dispatch's own fix 2) is the one seam that reaches back out to here.
    Called from the top of every `_tick`, from `AttemptManager` right before every terminal
    WorkerState transition, and from `_tick`'s own `stop()`/transport-closed branches right before
    `run()` returns, so a noted Alarm is never more than one of those checkpoints from actually
    being sent, in the order `take_pending_alarms` drains them. Module-level, reading `runtime`'s
    private state directly, the same way `hivemind.workers.runtime.attempt.AttemptManager` does
    (that module's own docstring), so `WorkerRuntime` itself stays within codingrules 5.1's size
    limit.
    """
    if runtime._reporter.assignment is None:
        return  # Defensive: nothing to report against yet (Reporter.require_assignment).
    for pending in runtime._ctx.telemetry.take_pending_alarms():
        try:
            await runtime._reporter.send_alarm(
                AlarmDetails(
                    kind=pending.kind,
                    detail=pending.detail,
                    reason="A Capping proposal was rolled back after applying.",
                )
            )
        except TransportClosedError:
            # Recoverable (this dispatch's own fix 2): Reporter.send_alarm already recorded
            # alarm.raised on the trail before attempting the wire send (fix 1), so the audit
            # record survives even when the link to this Worker's Warden is already gone -- most
            # often because this very flush is running from _on_transport_closed. Logged, not
            # raised, so a flush at a terminal transition or right before run() returns can never
            # crash this Worker's own tick loop.
            log.warning(
                "workers.runtime.alarm_send_skipped",
                worker_id=runtime._ctx.worker_id,
                kind=pending.kind.value,
            )
