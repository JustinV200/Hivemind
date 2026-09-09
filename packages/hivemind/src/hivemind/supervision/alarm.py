"""Mirror waggle's AlarmKind/AlarmSeverity, and define Alarm plus its one state machine.

An Alarm is an issue a bee (a Worker or a Warden) escalates to its supervisor because it cannot
resolve it on its own (codingrules section 8.8): the same alarm id, origin and attempt count
travel at every level, so no level handles it twice, and the human is always last, reached only
through the Queen. `AlarmKind` (what went wrong, the closed set the escalation policy keys on) and
`AlarmSeverity` (how bad) mirror `waggle.messages.supervision.AlarmKind` and
`waggle.messages.labels.AlarmSeverity` member for member (codingrules section 6.1: "hivemind
mirrors waggle's label enums with a sync test"), because the same two labels travel on
`AlarmRaised` over the wire. `AlarmContext` (typed references to the task, Cell, bee, trail event
and Handoff an Alarm concerns) is a value model with no behaviour and is imported from waggle
directly rather than mirrored, exactly like `ContextTelemetry` elsewhere in this subsystem.
`AlarmState` plus its transition table `TRANSITIONS` is the state machine codingrules section 9
requires for every machine in the Hive, tested edge by edge (Appendix C, the "Alarm" row); nothing
else in the Hive decides whether a transition is legal. Model and state machine share this one file
(rather than a package, codingrules 5.2: "a split made only for size... folds back once 5.1
allows it") because together they stay well under the 300-line limit.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by whichever bee first raises the
    issue (`Alarm.from_wire`) and read by every `Supervisor` implementation and by
    `hivemind.supervision.policy.decide`. Calls into `hivemind.cell` (for `HoneyClearance`) and
    `hivemind.supervision.errors` (for `InvalidAlarmTransitionError`) and waggle only.

Key invariants:
    - AlarmKind and AlarmSeverity mirror waggle.messages.supervision.AlarmKind and
      waggle.messages.labels.AlarmSeverity member for member
      (tests/unit/supervision/test_alarm.py checks both).
    - Alarm is frozen and forbids extras, like every boundary value in this repository.
    - Alarm.attempts is never negative; Field(ge=0) rejects a negative value before any other code
      sees it.
    - from_wire always builds an Alarm in AlarmState.RAISED: a wire AlarmRaised is, by definition,
      an Alarm that has not started HANDLING at the receiving level yet.
    - TRANSITIONS has exactly one entry per AlarmState member; RESOLVED maps to an empty
      frozenset, the one state an Alarm never leaves once it reaches it.
    - can_transition and assert_transition read TRANSITIONS only; neither hard-codes an edge.

See Also:
    - .claude/codingrules.md section 8.8 for the Alarm/escalation model this module implements.
    - .claude/codingrules.md section 9 for the state-machine shape this module follows.
    - .claude/codingrules.md Appendix C, "Alarm" row, for the transition table this module
      implements: "RAISED -> HANDLING -> RESOLVED; HANDLING -> ESCALATED -> HANDLING (at the next
      level)", and for this file being that row's named file.
    - waggle.messages.supervision for AlarmKind, AlarmContext and AlarmRaised, the wire forms this
      module mirrors or carries directly.
    - hivemind.supervision.errors for InvalidAlarmTransitionError, the error assert_transition
      raises.
    - hivemind.supervision.policy for decide, which reads Alarm.kind and Alarm.attempts.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from hivemind.cell import HoneyClearance
from hivemind.supervision.errors import InvalidAlarmTransitionError
from waggle.ids import AlarmId, WardenId, WorkerId
from waggle.messages import AlarmSeverity as WireAlarmSeverity
from waggle.messages.base import UtcDatetime
from waggle.messages.supervision import AlarmContext, AlarmRaised
from waggle.messages.supervision import AlarmKind as WireAlarmKind

__all__ = [
    "TRANSITIONS",
    "Alarm",
    "AlarmKind",
    "AlarmSeverity",
    "AlarmState",
    "assert_transition",
    "can_transition",
]

# The bee id shape origin round-trips through: a plain str on Alarm, WorkerId | WardenId on the
# wire. Named once so from_wire/to_wire cast to it instead of repeating the union inline.
_BeeId = WorkerId | WardenId


# ──────────────────────────────────────────────────────────────────────────────
# Mirrored kinds and the model
# ──────────────────────────────────────────────────────────────────────────────


class AlarmKind(Enum):
    """What went wrong, as the escalation policy keys it; OTHER holds anything not yet named.

    Mirrors waggle.messages.supervision.AlarmKind member for member.
    """

    WORKER_FAILED = "WORKER_FAILED"
    WORKER_CRASHED = "WORKER_CRASHED"
    WORKER_STALLED = "WORKER_STALLED"
    ACCEPTANCE_FAILED = "ACCEPTANCE_FAILED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    GRANT_EXCEEDED = "GRANT_EXCEEDED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AUDIT_FAILED = "AUDIT_FAILED"
    CELL_UNREACHABLE = "CELL_UNREACHABLE"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"  # A lease's scratch directory outgrew its configured quota.
    OTHER = "OTHER"  # Anything new, until a minor bump names it.


class AlarmSeverity(Enum):
    """How bad an Alarm is.

    Mirrors waggle.messages.labels.AlarmSeverity member for member.
    """

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Alarm(BaseModel):
    """An issue a bee escalated because it could not resolve it, tracked through its own lifecycle.

    The same id, origin and attempt count are preserved at every level (codingrules section 8.8);
    only `state` and, on a re-raise, `attempts` change as it climbs. Built from a received
    `AlarmRaised` via `from_wire`, and converted back for forwarding or for a matching
    `AlarmResolved` via `to_wire`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: AlarmId = Field(description="Identical at every level so no level handles it twice.")
    kind: AlarmKind = Field(description="What went wrong, as the policy table keys it.")
    severity: AlarmSeverity = Field(description="How bad.")
    origin: str = Field(description="The bee that first raised it, as a plain id string.")
    attempts: int = Field(
        ge=0, description="Resolution attempts made so far across levels; never negative."
    )
    context: AlarmContext = Field(
        description="Typed references to the task, Cell, bee, trail event and Handoff (waggle's "
        "own value model, carried directly)."
    )
    detail: str = Field(description="The failing assertion, error sentence or observation.")
    clearance: HoneyClearance = Field(description="The label of detail.")
    raised_at: UtcDatetime = Field(description="When it was first raised.")
    state: AlarmState = Field(
        description="Where it is in RAISED -> HANDLING -> RESOLVED, or -> ESCALATED -> HANDLING."
    )

    @classmethod
    def from_wire(cls, wire: AlarmRaised) -> Alarm:
        """Build an Alarm from a received AlarmRaised, starting in AlarmState.RAISED.

        Args:
            wire: The waggle.messages.supervision.AlarmRaised read off an Envelope.

        Returns:
            The equivalent Alarm, in AlarmState.RAISED: the receiving level has not started
            handling it yet.
        """
        # Both sides share field names, so the conversion is a straight field-by-field copy; the
        # two mirrored enums are mapped through their hivemind members by value.
        return cls(
            id=wire.alarm_id,
            kind=AlarmKind(wire.kind.value),
            severity=AlarmSeverity(wire.severity.value),
            origin=wire.origin,
            attempts=wire.attempts,
            context=wire.context,
            detail=wire.detail,
            clearance=HoneyClearance.from_wire(wire.clearance),
            raised_at=wire.raised_at,
            state=AlarmState.RAISED,
        )

    def to_wire(self, reason: str) -> AlarmRaised:
        """Build the AlarmRaised waggle carries when this Alarm is forwarded up the chain.

        Args:
            reason: Why the sender escalates instead of handling it; not stored on Alarm itself
                (codingrules section 8.8: the trail, not the model, is where a reason is audited),
                so a forwarding caller supplies it here.

        Returns:
            The equivalent waggle.messages.supervision.AlarmRaised, ready to send.
        """
        return AlarmRaised(
            alarm_id=self.id,
            kind=WireAlarmKind(self.kind.value),
            severity=WireAlarmSeverity(self.severity.value),
            origin=cast(_BeeId, self.origin),
            attempts=self.attempts,
            raised_at=self.raised_at,
            context=self.context,
            detail=self.detail,
            clearance=self.clearance.to_wire(),
            reason=reason,
        )


# ──────────────────────────────────────────────────────────────────────────────
# State machine
# ──────────────────────────────────────────────────────────────────────────────


class AlarmState(Enum):
    """Every state an Alarm can be in, from being raised to being resolved.

    See TRANSITIONS below for the legal moves between these; nowhere else decides that.
    """

    RAISED = "RAISED"  # Just escalated; the current supervisor has not started handling it.
    HANDLING = "HANDLING"  # The current supervisor is attempting to resolve it.
    ESCALATED = "ESCALATED"  # Forwarded to the next supervisor up the chain, same alarm id.
    RESOLVED = "RESOLVED"  # Terminal: closed, at every level that saw it.


# The single transition table (codingrules section 9): one entry per AlarmState, each edge
# commented with who or what causes it. This is the only place that decides whether a transition
# is legal; every caller goes through can_transition/assert_transition rather than comparing
# states directly.
TRANSITIONS: Mapping[AlarmState, frozenset[AlarmState]] = {
    AlarmState.RAISED: frozenset(
        {
            AlarmState.HANDLING,  # the supervisor starts working the Alarm
        }
    ),
    AlarmState.HANDLING: frozenset(
        {
            AlarmState.RESOLVED,  # the supervisor's action (or a self-clear) settled it
            AlarmState.ESCALATED,  # the supervisor's policy says escalate; same id goes up
        }
    ),
    AlarmState.ESCALATED: frozenset(
        {
            # The next supervisor up the chain receives the same alarm id and starts handling it
            # again; this is a fresh state object at that level, never a return to RAISED.
            AlarmState.HANDLING,
        }
    ),
    AlarmState.RESOLVED: frozenset(),  # terminal: nothing follows
}


def can_transition(from_state: AlarmState, to_state: AlarmState) -> bool:
    """Return whether TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The Alarm's current state.
        to_state: The state a caller wants to move it to.

    Returns:
        True if `to_state` is one of the edges TRANSITIONS lists for `from_state`.
    """
    return to_state in TRANSITIONS[from_state]


def assert_transition(
    from_state: AlarmState, to_state: AlarmState, alarm_id: str | None = None
) -> None:
    """Raise unless TRANSITIONS allows moving from `from_state` to `to_state`.

    Args:
        from_state: The Alarm's current state.
        to_state: The state a caller wants to move it to.
        alarm_id: The Alarm's id, when the caller has it, folded into the error message.

    Raises:
        InvalidAlarmTransitionError: `to_state` is not one of the edges TRANSITIONS lists for
            `from_state`, for instance moving a RESOLVED Alarm anywhere, or RAISED straight to
            RESOLVED without a HANDLING step in between.
    """
    # Every caller that advances an Alarm's state goes through this single check, so no edge is
    # ever legal anywhere the table itself does not list it.
    if not can_transition(from_state, to_state):
        raise InvalidAlarmTransitionError(from_state, to_state, alarm_id=alarm_id)
