"""Define WardenError and the ways a Warden's own bookkeeping can fail on purpose.

The Warden (the per-Cell supervisor, `hivemind.wardens.warden.Warden`) can fail in a few ways this
package raises on purpose: its own `WardenState` machine (`hivemind.wardens.state`) is asked for an
edge it does not have (`InvalidWardenTransitionError`), a `Supervisor.telemetry`/`inspect`/
`intervene` call names a sub-bee the Warden does not currently supervise
(`UnknownSubBeeError`), or its local pool (`hivemind.wardens.local_pool.pool.LocalPool`) is asked to
acquire a slot it has none of left (`LocalPoolExhaustedError`). Every subsystem roots its own error
tree at `hivemind.common.errors.HiveMindError` (codingrules section 10); this module is
`hivemind.wardens`'s own root plus its specific subclasses.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers). Raised by
    `hivemind.wardens.state.assert_transition`, `hivemind.wardens.warden.Warden` (the `Supervisor`
    methods) and `hivemind.wardens.local_pool.pool.LocalPool.acquire`; caught by whichever caller
    can recover (the Warden's own tick handlers convert a `LocalPoolExhaustedError` into "park the
    assignment" rather than letting it propagate). Calls into `hivemind.common.errors` only.

Key invariants:
    - Every WardenError subclass sets its own `code`; none shares a code with another.
    - InvalidWardenTransitionError always names both the state it moved from and the state it was
      asked to move to, so the failure is debuggable without a stack trace.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError and ConflictError, the roots this module's classes
      descend from.
    - hivemind.wardens.state for TRANSITIONS and assert_transition, InvalidWardenTransitionError's
      one caller.
    - hivemind.wardens.local_pool.pool for LocalPool, LocalPoolExhaustedError's one caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from hivemind.common.errors import ConflictError, HiveMindError, NotFoundError

if TYPE_CHECKING:
    # Only for the type hints below: hivemind.wardens.state imports this module for
    # InvalidWardenTransitionError, so a real (non-TYPE_CHECKING) import here would cycle back.
    from hivemind.wardens.state import WardenState

__all__ = [
    "InvalidWardenTransitionError",
    "LocalPoolExhaustedError",
    "UnknownSubBeeError",
    "WardenError",
]


class WardenError(HiveMindError):
    """Root of every error `hivemind.wardens` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.wardens.error"


class InvalidWardenTransitionError(ConflictError):
    """Raise when the Warden state machine is asked for an edge its transition table does not have.

    Raised by `hivemind.wardens.state.assert_transition`, mirroring
    `hivemind.workers.errors.InvalidWorkerTransitionError` field for field.
    """

    code: ClassVar[str] = "hivemind.wardens.invalid_transition"

    def __init__(
        self, from_state: WardenState, to_state: WardenState, warden_id: str | None = None
    ) -> None:
        """Build the error for a forbidden Warden state transition.

        Args:
            from_state: The WardenState the machine was in.
            to_state: The WardenState a caller asked to move to.
            warden_id: The Warden's id, when the caller has it, folded into the message so the
                failure is debuggable without a stack trace.
        """
        # A missing warden_id still produces a full sentence; codingrules section 10 wants the
        # identifiers needed to debug, not a placeholder, so the clause is only added when known.
        subject = f" for {warden_id}" if warden_id is not None else ""
        message = (
            f"Cannot transition{subject} from {from_state.name} to {to_state.name}: no such "
            "edge exists in the Warden state machine."
        )
        super().__init__(message)
        self.from_state = from_state
        self.to_state = to_state
        self.warden_id = warden_id


class UnknownSubBeeError(NotFoundError):
    """Raise when a Supervisor call names a sub-bee this Warden does not currently supervise.

    Raised by `hivemind.wardens.warden.Warden.telemetry`/`.inspect`/`.intervene`
    (`hivemind.supervision.supervisor.Supervisor`'s own contract: "raises UnknownChildError when
    `child` names nothing it supervises" -- this is the Warden-side equivalent, rooted at
    `NotFoundError` rather than `hivemind.supervision.errors.UnknownChildError` because that class
    is itself rooted at a different subsystem's tree and codingrules section 10 wants every
    subsystem's own root).
    """

    code: ClassVar[str] = "hivemind.wardens.unknown_sub_bee"

    def __init__(self, worker_id: str) -> None:
        """Build the error for a sub-bee id this Warden has never spawned or has already reaped.

        Args:
            worker_id: The id a caller passed that names no current sub-bee.
        """
        super().__init__(f"Warden supervises no sub-bee {worker_id}.")
        self.worker_id = worker_id


class LocalPoolExhaustedError(WardenError):
    """Raise when the Warden's local pool has no sub-bee slot left to acquire.

    Raised by `hivemind.wardens.local_pool.pool.LocalPool.acquire` only through the boolean it
    returns in production code; this exception exists for a caller (a test, or a future dispatch)
    that wants to fail loudly instead of checking the boolean, and is never raised by the Warden's
    own tick handlers, which treat a full pool as "park the assignment", not a failure.
    """

    code: ClassVar[str] = "hivemind.wardens.local_pool_exhausted"

    def __init__(self, capacity: int) -> None:
        """Build the error for a local pool with no free slot.

        Args:
            capacity: The pool's configured `max_sub_bees`, folded into the message.
        """
        super().__init__(f"Local pool is at capacity ({capacity} sub-bees); no slot to acquire.")
        self.capacity = capacity
