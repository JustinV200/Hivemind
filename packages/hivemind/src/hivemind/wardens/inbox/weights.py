"""Build the Warden's own Attendant and wrap a received Envelope as one InboxItem.

Codingrules section 8.8: "A Warden's Attendant runs the same scoring over a smaller, more uniform
inbox and consults a model only if its grant allows it; by default it is autopilot-only." This
module is the wardens-side half of that: `warden_attendant` builds an `hivemind.supervision.
attendant.Attendant` over `hivemind.supervision.attendant.WeightTable.warden_default()` with no
`TieBreaker` (roadmap step 3.19: a Warden's own Attendant over sub-bee heartbeats, results,
Alarms, Queen messages and timers is autopilot-only by default), and `to_inbox_item` classifies
one received `waggle.envelope.Envelope`'s payload into the
`InboxKind`, severity and task linkage `hivemind.supervision.attendant.score_item` needs, without
either the Warden or this module having to know the payload's exact wire shape beyond an isinstance
check.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's inbox
    sub-package. `warden_attendant` is called once by `hivemind.wardens.warden.Warden.__init__`;
    `to_inbox_item` is called by its tick for every envelope drained off the queen link or a
    sub-bee's own link. Calls into `hivemind.supervision` (Alarm, AlarmSeverity), `hivemind.
    supervision.attendant` (Attendant, InboxItem, InboxKind, WeightTable) and waggle only.

Key invariants:
    - `to_inbox_item` never raises on an unrecognised payload type: it falls back to
      `InboxKind.WAGGLE_MESSAGE` with no severity and no task linkage, so `hivemind.wardens.
      autopilot.table.decide`'s own `NEEDS_JUDGEMENT` fallback is what handles an unknown kind,
      not a crash here.
    - `warden_attendant` never passes a `TieBreaker`: a Warden's Attendant is autopilot-only by
      default (codingrules section 8.8); a later roadmap phase may enable one within a grant.

See Also:
    - .claude/codingrules.md section 8.8 for the Attendant shape this module builds for a Warden.
    - hivemind.supervision.attendant for Attendant, InboxItem, InboxKind, WeightTable and
      score_item, the machinery this module feeds.
    - hivemind.wardens.warden for Warden, the one caller of both functions here.
"""

from __future__ import annotations

from hivemind.supervision import Alarm, AlarmSeverity
from hivemind.supervision.attendant import Attendant, InboxItem, InboxKind, WeightTable
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.messages.supervision import AlarmRaised, Answer, Question
from waggle.messages.supervision.oversight import Intervene
from waggle.messages.task import TaskAssign, TaskCancel, TaskPause, TaskResult, TaskResume

__all__ = ["to_inbox_item", "warden_attendant"]


def warden_attendant(clock: Clock) -> Attendant:
    """Build this Warden's own Attendant: `WeightTable.warden_default()`, no tie-breaker.

    Args:
        clock: Source of `now` for `Attendant.order`.

    Returns:
        An Attendant scoring a Warden's smaller, more uniform inbox, autopilot-only.
    """
    return Attendant(WeightTable.warden_default(), clock)


def to_inbox_item(envelope: Envelope, principal: str) -> InboxItem:
    """Wrap `envelope` as one InboxItem, classified by its payload's own type.

    Args:
        envelope: One envelope drained off the queen link or a sub-bee's own link.
        principal: Who this item is from: `"queen"` for the queen link, or the sending sub-bee's
            worker id for a sub-bee's own link.

    Returns:
        A validated InboxItem, ready for `Attendant.order`.
    """
    payload = envelope.payload
    kind, severity, task_id = _classify(payload)
    return InboxItem(
        id=envelope.id,
        kind=kind,
        received_at=envelope.sent_at,
        principal=principal,
        severity=severity,
        task_id=task_id,
        latency_budget_s=None,
        payload_kind=envelope.kind,
        payload=payload,
    )


def _classify(payload: object) -> tuple[InboxKind, AlarmSeverity | None, object | None]:
    """Return `(InboxKind, severity, task_id)` for `payload`'s own type.

    Unrecognised payload types fall back to `(WAGGLE_MESSAGE, None, None)` rather than raising:
    `hivemind.wardens.autopilot.table.decide`'s own `NEEDS_JUDGEMENT` case is what a truly novel
    kind is for.
    """
    if isinstance(payload, AlarmRaised):
        alarm = Alarm.from_wire(payload)
        return InboxKind.ALARM, alarm.severity, payload.context.task_id
    if isinstance(payload, Question):
        return InboxKind.QUESTION, None, payload.task_id
    if isinstance(payload, Answer):
        return InboxKind.WAGGLE_MESSAGE, None, payload.task_id
    if isinstance(payload, Intervene):
        # A Queen's lever names the task it targets; without this branch every Intervene was
        # unclassified and the Warden could not find the sub-bee to apply it to.
        return InboxKind.WAGGLE_MESSAGE, None, payload.task_id
    if isinstance(payload, TaskAssign | TaskResult):
        return InboxKind.WAGGLE_MESSAGE, None, payload.task_id
    if isinstance(payload, TaskCancel | TaskPause | TaskResume):
        return InboxKind.WAGGLE_MESSAGE, None, payload.task_id
    return InboxKind.WAGGLE_MESSAGE, None, None
