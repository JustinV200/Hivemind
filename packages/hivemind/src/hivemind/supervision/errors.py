"""Define SupervisionError and the supervision subsystem's own error tree.

Supervision (`hivemind.supervision`) is the one `Supervisor` protocol used at every level of the
tree (human, Queen, a Warden, a sub-bee): children, telemetry, inspection, intervention, the Alarm
state machine, the escalation policy and the Attendant (a supervisor's inbox triage). This module
holds the ways a caller can misuse that surface on purpose: asking the Alarm state machine
(`alarm.state.TRANSITIONS`) for an edge it does not have, loading or evaluating a malformed
`EscalationPolicy`, or addressing a child a `Supervisor` does not know about. Every one of these is
`SupervisionError`, the subsystem's own root, so a caller several layers up can catch one name and
know it caught anything supervision itself raised on purpose (codingrules section 10).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by `hivemind.supervision.alarm`
    (the state machine), `hivemind.supervision.policy` (loading and deciding) and every
    `Supervisor` implementation (the Queen, a Warden, `FakeSupervisor`) when a child id is unknown.
    Imported by every layer above that calls into supervision.

Key invariants:
    - Every SupervisionError subclass sets its own `code`; none shares a code with another.
    - InvalidAlarmTransitionError and UnknownChildError each subclass one of
      `hivemind.common.errors`' base categories (ConflictError, NotFoundError) because a forbidden
      transition and a missing child map cleanly onto one of those; PolicyError does not, because
      a malformed or ambiguous escalation policy is specific to `hivemind.supervision.policy` and
      no base category fits it.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError, ConflictError and NotFoundError, the roots this
      module's classes descend from.
    - hivemind.supervision.alarm for TRANSITIONS, the table InvalidAlarmTransitionError reports.
    - hivemind.supervision.policy for EscalationPolicy and decide, which raise PolicyError.
"""

from __future__ import annotations

from enum import Enum
from typing import ClassVar

from hivemind.common.errors import ConflictError, HiveMindError, NotFoundError

__all__ = [
    "InvalidAlarmTransitionError",
    "PolicyError",
    "SupervisionError",
    "UnknownChildError",
]


class SupervisionError(HiveMindError):
    """Root of every error `hivemind.supervision` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.supervision.error"


class InvalidAlarmTransitionError(ConflictError):
    """Raise when the Alarm state machine is asked for an edge its transition table does not have.

    Raised by `hivemind.supervision.alarm.assert_transition`.
    """

    code: ClassVar[str] = "hivemind.supervision.invalid_alarm_transition"

    def __init__(self, from_state: Enum, to_state: Enum, alarm_id: str | None = None) -> None:
        """Build the error for a forbidden Alarm transition.

        Args:
            from_state: The AlarmState the machine was in.
            to_state: The AlarmState a caller asked to move to.
            alarm_id: The Alarm's id, when the caller has it, folded into the message so the
                failure is debuggable without a stack trace.
        """
        # A missing alarm_id still produces a full sentence; codingrules section 10 wants the
        # identifiers needed to debug, not a placeholder, so the clause is only added when known.
        subject = f" for {alarm_id}" if alarm_id is not None else ""
        message = (
            f"Cannot transition{subject} from {from_state.name} to {to_state.name}: no such "
            "edge exists in the Alarm state machine."
        )
        super().__init__(message)
        self.from_state = from_state
        self.to_state = to_state
        self.alarm_id = alarm_id


class PolicyError(SupervisionError):
    """Raise when an `EscalationPolicy` cannot be loaded from TOML or is malformed.

    Raised by `hivemind.supervision.policy.load_policy` for a missing file, a TOML syntax error,
    or a document that fails `EscalationPolicy`'s own pydantic validation.
    """

    code: ClassVar[str] = "hivemind.supervision.policy_error"


class UnknownChildError(NotFoundError):
    """Raise when a `Supervisor` is addressed about a child id it does not supervise.

    Raised by `telemetry`, `inspect` and `intervene` on every `Supervisor` implementation
    (the Queen, over Wardens; a Warden, over its sub-bees; `FakeSupervisor`, in tests).
    """

    code: ClassVar[str] = "hivemind.supervision.unknown_child"

    def __init__(self, child: str) -> None:
        """Build the error for an unrecognised child id.

        Args:
            child: The id that was addressed and not found among this supervisor's children.
        """
        super().__init__(f"No child with id {child!r} is known to this supervisor.")
        self.child = child
