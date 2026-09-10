"""Define AttemptManager: WorkerRuntime's own delegate for one role attempt's whole lifecycle.

`AttemptManager` is split out of `hivemind.workers.runtime.loop.WorkerRuntime` only for size
(codingrules section 5.2: "a module that outgrows 5.1 splits into a sibling inside its package"):
starting the role's own coroutine, scheduling and forcing a `TaskCancel`/`Intervene(CANCEL)`'s
grace period, and interpreting the role task once it finishes -- cancelled, crashed, or a
`WorkerOutcome` (claimed, or a handoff to checkpoint and either restart or close) -- is one
coherent responsibility on its own, but does not fit `WorkerRuntime`'s own 200-line class limit
alongside the tick loop and its wire-message dispatch. It is `WorkerRuntime`'s own delegate, not a
general-purpose class: it reads and writes `WorkerRuntime`'s private state directly (`_ctx`,
`_deps`, `_worker`, `_reporter`), the same way one of `WorkerRuntime`'s own private methods would,
and only one `WorkerRuntime` ever holds a reference to it. Every state change and outgoing message
goes through `runtime._reporter` (`hivemind.workers.runtime.reporter.Reporter`), the same delegate
`WorkerRuntime`'s own handlers report through, so both classes move one Worker's state and send
its messages through one shared implementation.

Fits into the Hive:
    Layer 4 (roles that do the work). Owned by exactly one `WorkerRuntime` instance (roadmap step
    3.15), constructed in its `__init__` and driven from its `_tick`. Calls into `hivemind.llm.
    errors` (ProviderUnavailableError, RateLimitedError -- `_alarm_kind_for_crash`'s own
    classification table, this dispatch's own fix 3a; workers may import hivemind.llm),
    `hivemind.memory`, `hivemind.supervision.alarm` and waggle only; the `WorkerRuntime` type it
    is built with is imported under `TYPE_CHECKING` only, so no runtime import cycle exists
    between this module and `hivemind.workers.runtime.loop`.

Key invariants:
    - `on_finished` is only ever called once the owning `_tick` has confirmed `role_task` is done
      (via `asyncio.wait`); it never awaits the task itself, only inspects it
      (`asyncio.Task.cancelled`/`exception`/`result`), so a role's own exception never needs an
      `except Exception` clause here.
    - `request_cancel` schedules at most one cancel-deadline task per attempt: a second request
      (TaskCancel followed by an Intervene(CANCEL), say) changes nothing further.
    - `_stop_after_handoff` is consulted, then reset, exactly once, inside `_finish_stopped`: an
      Intervene(HANDOFF/REBIND/TAKEOVER) that arrives after the attempt has already stopped has
      nothing left to affect.
    - Only `_finish_claimed` ever sends a TaskResult: `waggle.messages.task.reports.TaskResult`'s
      own validator requires `checked_by` set on any outcome but CLAIMED, and a Worker's own hop
      always carries `checked_by=None` (that field's docstring: "None on the hop from the
      Worker, which may only claim"). `_handle_crashed`, `_finish_stopped` and `_finish_killed`
      close the attempt locally (a WorkerState transition and its trail event) and, for a crash,
      an AlarmRaised; the Warden is the only hop that ever reports FAILED or CANCELLED.
    - Every terminal transition (`_finish_claimed`, `_handle_crashed`, `_finish_stopped`,
      `_finish_killed`) flushes `runtime`'s own pending-Alarm queue first, via
      `WorkerRuntime._drain_pending_alarms` (this dispatch's own fix 2): the top-of-tick drain in
      `hivemind.workers.runtime.loop.WorkerRuntime._tick` only catches an Alarm noted before the
      role's own task was observed done, so a role that notes one on its very last tool call and
      then returns needs this second checkpoint or the Alarm is never sent at all.

See Also:
    - .claude/codingrules.md section 5.2 for the module-split rule this class follows.
    - .claude/codingrules.md Appendix C, "Worker" row, for the states this class's methods move
      `WorkerRuntime` through (via `Reporter.transition`).
    - hivemind.workers.runtime.loop for WorkerRuntime, this class's one owner.
    - hivemind.workers.runtime.reporter for Reporter, the delegate this class reports through.
    - hivemind.workers.runtime.reports for ResultDetails/AlarmDetails, the report shapes this
      class builds.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from hivemind.llm.errors import ProviderUnavailableError, RateLimitedError
from hivemind.memory import Handoff, write_checkpoint
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.runtime.reports import AlarmDetails, ResultDetails
from hivemind.workers.state import WorkerState
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskOutcome, TaskStage

# A crashed role's own exception, classified into the AlarmKind its Warden's policy keys on (this
# dispatch's own fix 3a): a table, not an isinstance chain, so a new provider-shaped error only
# ever needs a new row here. ProviderUnavailableError/RateLimitedError name a bound provider
# actually being unreachable or throttled; anything else (a bare RuntimeError, an assertion, a
# provider-neutral bug) stays WORKER_CRASHED, the closed-set default for "something broke that is
# not about the model behind this attempt."
_CRASH_ALARM_KINDS: tuple[tuple[type[BaseException], AlarmKind], ...] = (
    (ProviderUnavailableError, AlarmKind.PROVIDER_UNAVAILABLE),
    (RateLimitedError, AlarmKind.PROVIDER_UNAVAILABLE),
)

if TYPE_CHECKING:
    # Type-checking only: WorkerRuntime imports AttemptManager for real, so a runtime import here
    # would cycle back (see the module docstring).
    from hivemind.workers.runtime.loop import WorkerRuntime

__all__ = ["AttemptManager"]


class AttemptManager:
    """Own one role attempt's task handle and cancel/handoff-stop flags for one WorkerRuntime.

    Owns its own mutable state in place (codingrules section 8.5): `_role_task`,
    `_cancel_deadline_task`, `_cancel_reason`, `_stop_after_handoff` and `_stop_reason` all change
    as the attempt runs.
    """

    def __init__(self, runtime: WorkerRuntime) -> None:
        """Build an AttemptManager for `runtime`, with no attempt running yet.

        Args:
            runtime: The one WorkerRuntime this manager reports through.
        """
        self._runtime = runtime
        self._role_task: asyncio.Task[WorkerOutcome] | None = None
        self._cancel_deadline_task: asyncio.Task[None] | None = None
        self._cancel_reason = ""
        self._stop_after_handoff = False
        self._stop_reason = ""

    @property
    def role_task(self) -> asyncio.Task[WorkerOutcome] | None:
        """The role's own in-flight coroutine, as an asyncio Task; None between attempts."""
        return self._role_task

    @property
    def cancel_deadline_task(self) -> asyncio.Task[None] | None:
        """The scheduled cancel-grace sleep, as an asyncio Task; None while none is pending."""
        return self._cancel_deadline_task

    def start(self, resume_from: Handoff | None) -> None:
        """Spawn the role's own coroutine as this manager's one owned role task.

        Args:
            resume_from: The Handoff to resume from; None for a fresh start.
        """
        runtime = self._runtime
        runtime._ctx.telemetry.handoff_requested = False
        assignment = runtime._reporter.require_assignment()
        self._role_task = asyncio.ensure_future(
            runtime._worker.run(runtime._ctx, assignment, resume_from)
        )

    def request_cancel(self, reason: str, grace_s: float) -> None:
        """Ask for cancellation and, if a role is running, schedule its forced cancel.

        Args:
            reason: Why this attempt is being cancelled.
            grace_s: Seconds the role gets to checkpoint before it is force-cancelled.
        """
        runtime = self._runtime
        runtime._ctx.telemetry.cancel_requested = True
        self._cancel_reason = reason
        if self._role_task is None or self._role_task.done():
            return  # Nothing running to cancel; a finished role's own handler already reported.
        if self._cancel_deadline_task is not None:
            return  # A cancel is already scheduled; a second request changes nothing further.
        self._cancel_deadline_task = asyncio.ensure_future(runtime._deps.clock.sleep(grace_s))

    def force_cancel_if_due(self) -> None:
        """Force-cancel the role's task now that its cancel grace period has elapsed."""
        self._cancel_deadline_task = None
        if self._role_task is not None and not self._role_task.done():
            self._role_task.cancel()

    def request_handoff(self, *, stop_after: bool, reason: str = "") -> None:
        """Ask the role for a handoff at its own next opportunity, from an Intervene.

        Args:
            stop_after: True for Handoff/Rebind/Takeover (stop once the handoff is written);
                False for Compact/Checkpoint (write it and continue).
            reason: Why the attempt stops after this handoff; ignored when `stop_after` is False.
        """
        self._runtime._ctx.telemetry.handoff_requested = True
        if stop_after:
            self._stop_after_handoff = True
            self._stop_reason = reason

    async def on_finished(self) -> None:
        """Interpret the finished role task: cancelled, crashed, or a WorkerOutcome."""
        task = self._role_task
        if task is None:
            return  # The owning _tick only calls this when role_task is set; defensive no-op.
        self._role_task = None
        self._clear_cancel_deadline()
        if task.cancelled():
            await self._finish_killed()
            return
        # Task.exception() reads a captured exception back without re-raising it, so a role that
        # crashed never needs an `except Exception` clause here at all (module docstring).
        error = task.exception()
        if error is not None:
            await self._handle_crashed(error)
            return
        await self._handle_outcome(task.result())

    def _clear_cancel_deadline(self) -> None:
        """Cancel and drop any scheduled cancel-deadline task; the role just ended on its own."""
        if self._cancel_deadline_task is not None and not self._cancel_deadline_task.done():
            self._cancel_deadline_task.cancel()
        self._cancel_deadline_task = None

    async def _handle_crashed(self, error: BaseException) -> None:
        """Move RUNNING -> FAILED and raise an Alarm.

        Sends no TaskResult: `waggle.messages.task.reports.TaskResult`'s own validator requires
        `checked_by` to be set on any outcome but CLAIMED, and a Worker's own hop always carries
        `checked_by=None` (that field's own docstring: "None on the hop from the Worker, which
        may only claim"). A Worker can therefore never legally report FAILED over the wire; the
        AlarmRaised is what tells the Warden, whose own escalation policy decides retry, respawn
        or escalate, and only the Warden's hop ever closes a task with a non-CLAIMED TaskResult.
        """
        runtime = self._runtime
        await runtime._drain_pending_alarms()  # Fix 2: flush before the transition.
        runtime._reporter.transition(WorkerState.FAILED)
        await runtime._reporter.record_event("worker.failed")
        await runtime._reporter.send_alarm(
            AlarmDetails(
                kind=_alarm_kind_for_crash(error),
                detail=str(error),
                reason=f"{type(error).__name__} raised while running the role.",
            )
        )

    async def _handle_outcome(self, outcome: WorkerOutcome) -> None:
        """Dispatch a returned WorkerOutcome: a claim of completion, or a request to hand off."""
        if outcome.claimed:
            await self._finish_claimed(outcome)
        else:
            await self._handle_handoff(outcome)

    async def _finish_claimed(self, outcome: WorkerOutcome) -> None:
        """Move RUNNING -> DONE and report TaskResult(CLAIMED)."""
        runtime = self._runtime
        await runtime._drain_pending_alarms()  # Fix 2: flush before the transition.
        runtime._reporter.transition(WorkerState.DONE)
        await runtime._reporter.record_event("worker.done")
        await runtime._reporter.send_result(
            ResultDetails(
                outcome=TaskOutcome.CLAIMED,
                summary=outcome.summary,
                reason="The role completed its work.",
                artifacts=outcome.artifacts,
                spend=outcome.spend_usd,
            )
        )

    async def _handle_handoff(self, outcome: WorkerOutcome) -> None:
        """Checkpoint the role's Handoff, then either restart it or close the attempt."""
        runtime = self._runtime
        handoff = outcome.handoff
        if handoff is None:  # WorkerOutcome's own validator forbids this; defensive no-op.
            return
        runtime._reporter.transition(WorkerState.HANDING_OFF)
        await runtime._reporter.record_event("worker.handing_off")
        assignment = runtime._reporter.require_assignment()
        ref = await write_checkpoint(
            handoff, assignment.task_id, runtime._reporter.memory_context()
        )
        await runtime._reporter.send_progress(TaskStage.CHECKPOINTED, "Checkpointed.", handoff=ref)
        if self._stop_after_handoff or runtime._ctx.telemetry.cancel_requested:
            await self._finish_stopped()
            return
        runtime._reporter.transition(WorkerState.RUNNING)
        await runtime._reporter.record_event("worker.resumed")
        self.start(handoff)

    async def _finish_stopped(self) -> None:
        """Move HANDING_OFF -> DONE once an Intervene asked this attempt to stop.

        Sends no TaskResult, for the same reason `_handle_crashed` sends none (see that
        docstring): a Worker's hop may only ever claim. The Warden pulled this lever itself
        (Handoff/Rebind/Takeover), so it already knows to stop; the `TaskProgress(CHECKPOINTED)`
        already sent carries the `HandoffRef` it needs to resume the work itself.
        """
        runtime = self._runtime
        await runtime._drain_pending_alarms()  # Fix 2: flush before the transition.
        runtime._reporter.transition(WorkerState.DONE)
        await runtime._reporter.record_event("worker.done", stop_reason=self._stop_reason[:200])
        self._stop_after_handoff = False
        self._stop_reason = ""

    async def _finish_killed(self) -> None:
        """Move the current state -> KILLED once TaskCancel/Intervene(CANCEL) has taken effect.

        Sends no TaskResult, for the same reason `_handle_crashed` sends none (see that
        docstring): the Warden already knows, since it is the one that asked for the cancel.
        """
        runtime = self._runtime
        await runtime._drain_pending_alarms()  # Fix 2: flush before the transition.
        runtime._reporter.transition(WorkerState.KILLED)
        await runtime._reporter.record_event(
            "worker.killed", cancel_reason=(self._cancel_reason or "Cancelled.")[:200]
        )


def _alarm_kind_for_crash(error: BaseException) -> AlarmKind:
    """Classify a crashed role's own exception into the AlarmKind its Warden's policy keys on.

    Args:
        error: The exception `AttemptManager.on_finished` caught from the role's own task.

    Returns:
        `AlarmKind.PROVIDER_UNAVAILABLE` for a typed `_CRASH_ALARM_KINDS` match (module-level
        table); `AlarmKind.WORKER_CRASHED` for anything else.
    """
    for error_type, kind in _CRASH_ALARM_KINDS:
        if isinstance(error, error_type):
            return kind
    return AlarmKind.WORKER_CRASHED
