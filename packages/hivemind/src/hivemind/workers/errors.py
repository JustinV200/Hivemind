"""Define WorkerError and the ways a Worker's own runtime can fail on purpose.

A Worker (a subagent a Warden spawns to run one role, such as a Drone) can fail in exactly two
ways this package raises on purpose: its own `WorkerState` machine (`hivemind.workers.state`) is
asked for an edge it does not have (`InvalidWorkerTransitionError`), or its runtime deliberately
stopped a role's own coroutine because the task was cancelled (`WorkerCancelledError`) --
`hivemind.workers.telemetry.TelemetryTracker.wait_if_paused` raises the latter so a role that
polls it between turns notices a pending cancellation as a typed error rather than hanging until
`hivemind.workers.runtime` force-cancels its asyncio task after the grace period. Every subsystem
roots its own error tree at `hivemind.common.errors.HiveMindError` (codingrules section 10); this
module is `hivemind.workers`'s own root plus its two specific subclasses.

Fits into the Hive:
    Layer 4 (roles that do the work). Raised by `hivemind.workers.state.assert_transition` and
    `hivemind.workers.telemetry.TelemetryTracker`; caught by `hivemind.workers.runtime` (the
    WorkerRuntime, roadmap step 3.15) so one bad wire message or one deliberate cancellation never
    crashes the whole runtime. Calls into `hivemind.common.errors` only.

Key invariants:
    - Every WorkerError subclass sets its own `code`; none shares a code with another.
    - InvalidWorkerTransitionError always names both the state it moved from and the state it was
      asked to move to, so the failure is debuggable without a stack trace.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError and ConflictError, the roots this module's classes
      descend from.
    - hivemind.workers.state for TRANSITIONS and assert_transition, InvalidWorkerTransitionError's
      one caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from hivemind.common.errors import ConflictError, HiveMindError

if TYPE_CHECKING:
    # Only for the type hints below: hivemind.workers.state imports this module for
    # InvalidWorkerTransitionError, so a real (non-TYPE_CHECKING) import here would cycle back.
    from hivemind.workers.state import WorkerState

__all__ = ["InvalidWorkerTransitionError", "WorkerCancelledError", "WorkerError"]


class WorkerError(HiveMindError):
    """Root of every error `hivemind.workers` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.workers.error"


class InvalidWorkerTransitionError(ConflictError):
    """Raise when the Worker state machine is asked for an edge its transition table does not have.

    Raised by `hivemind.workers.state.assert_transition`, mirroring
    `hivemind.supervision.errors.InvalidAlarmTransitionError` and
    `hivemind.cell.errors.InvalidLeaseTransitionError` field for field.
    """

    code: ClassVar[str] = "hivemind.workers.invalid_transition"

    def __init__(
        self, from_state: WorkerState, to_state: WorkerState, worker_id: str | None = None
    ) -> None:
        """Build the error for a forbidden Worker state transition.

        Args:
            from_state: The WorkerState the machine was in.
            to_state: The WorkerState a caller asked to move to.
            worker_id: The Worker's id, when the caller has it, folded into the message so the
                failure is debuggable without a stack trace.
        """
        # A missing worker_id still produces a full sentence; codingrules section 10 wants the
        # identifiers needed to debug, not a placeholder, so the clause is only added when known.
        subject = f" for {worker_id}" if worker_id is not None else ""
        message = (
            f"Cannot transition{subject} from {from_state.name} to {to_state.name}: no such "
            "edge exists in the Worker state machine."
        )
        super().__init__(message)
        self.from_state = from_state
        self.to_state = to_state
        self.worker_id = worker_id


class WorkerCancelledError(WorkerError):
    """Raise to unblock a role's own coroutine promptly once cancellation has been requested.

    `hivemind.workers.telemetry.TelemetryTracker.wait_if_paused` raises this when
    `cancel_requested` is set, so a role that calls it between turns (as every role should) sees a
    typed reason instead of blocking on a pause that will never lift, or instead of only noticing
    a cancellation when `hivemind.workers.runtime.WorkerRuntime` force-cancels its asyncio task
    after `TaskCancel`'s (or `Intervene(CANCEL)`'s) grace period. The runtime treats a role task
    that ends this way the same as one it force-cancelled: a TaskResult(CANCELLED) and the Worker
    state machine's KILLED state, never FAILED.
    """

    code: ClassVar[str] = "hivemind.workers.cancelled"

    def __init__(self, reason: str) -> None:
        """Build the error for a role's coroutine noticing a pending cancellation.

        Args:
            reason: Why the run was cancelled, copied from the TaskCancel/Intervene that asked
                for it.
        """
        super().__init__(f"Worker run was cancelled: {reason}")
        self.reason = reason
