"""Build the Queen's own Attendant and wrap a received Envelope as one InboxItem.

Codingrules section 8.8: "The Queen's Attendant weighs human messages heavily but not absolutely
and may use `ModelSlot.ATTENDANT` for ties and unknown kinds." This module is the queen-side half
of that: `queen_attendant` builds an `hivemind.supervision.attendant.Attendant` over
`hivemind.supervision.attendant.WeightTable.queen_default()`, with an optional
`hivemind.supervision.attendant.TieBreaker` (`hivemind.queen.inbox.tie_breaker.ModelTieBreaker` in
production), and `to_inbox_item` classifies one received `waggle.envelope.Envelope`'s payload into
the `InboxKind`, severity and task linkage `hivemind.supervision.attendant.score_item` needs,
without the Queen having to know each payload's exact wire shape beyond an `isinstance` check. A
`waggle.messages.cell.CellWaxProposed` (roadmap step 4.2a) classifies the same as any other
routine Waggle message, so it is scored low by construction rather than through a dedicated rule.
A human's chat message (`waggle.messages.control.HumanMessage`, roadmap step 10.5) is its own
`InboxKind.HUMAN_MESSAGE`, which `WeightTable.queen_default()` already weighs heavily but not
absolutely; `human_inbox_item` wraps one read from the chat log (it arrives through her wake
signal, not a Warden link, so there is no envelope), always under the `human` principal.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's inbox
    sub-package (which MAY import `hivemind.llm`; only `autopilot/` may not). `queen_attendant` is
    called once by `hivemind.queen.queen.Queen.__init__`; `to_inbox_item` is called by its tick for
    every envelope drained off an attached Warden's own link, and `human_inbox_item` by
    `hivemind.queen.ticks.chat` for every waiting human message. Calls into `hivemind.supervision`
    (Alarm, AlarmSeverity), `hivemind.supervision.attendant` (Attendant, InboxItem, InboxKind,
    TieBreaker, WeightTable) and waggle only.

Key invariants:
    - `to_inbox_item` never raises on an unrecognised payload type: it falls back to
      `InboxKind.WAGGLE_MESSAGE` with no severity and no task linkage, so
      `hivemind.queen.autopilot.table.decide`'s own `NEEDS_JUDGEMENT` fallback is what handles an
      unknown kind, not a crash here.

See Also:
    - .claude/codingrules.md section 8.8 for the Attendant shape this module builds for the Queen.
    - hivemind.supervision.attendant for Attendant, InboxItem, InboxKind, WeightTable and
      score_item, the machinery this module feeds.
    - hivemind.queen.inbox.tie_breaker for ModelTieBreaker, the model-backed TieBreaker.
    - hivemind.queen.queen for Queen, the one caller of both functions here.
"""

from __future__ import annotations

from datetime import datetime

from hivemind.supervision import Alarm, AlarmSeverity
from hivemind.supervision.attendant import Attendant, InboxItem, InboxKind, TieBreaker, WeightTable
from waggle.clock import Clock
from waggle.envelope import Envelope
from waggle.messages.cell import CellWaxProposed
from waggle.messages.control import HumanMessage
from waggle.messages.registry import kind_for
from waggle.messages.supervision import AlarmRaised, Answer, Question
from waggle.messages.task import TaskResult

HUMAN_PRINCIPAL = "human"  # The principal every human message is scored and addressed under.

__all__ = ["HUMAN_PRINCIPAL", "human_inbox_item", "queen_attendant", "to_inbox_item"]


def queen_attendant(clock: Clock, tie_breaker: TieBreaker | None = None) -> Attendant:
    """Build the Queen's own Attendant: `WeightTable.queen_default()`, an optional tie-breaker.

    Args:
        clock: Source of `now` for `Attendant.order`.
        tie_breaker: The model-backed arbiter for an exact score tie; None falls back to
            `(received_at, id)`, matching `Attendant`'s own contract.

    Returns:
        An Attendant scoring the Queen's own inbox: human messages heavy but not absolute.
    """
    return Attendant(WeightTable.queen_default(), clock, tie_breaker)


def to_inbox_item(envelope: Envelope, principal: str) -> InboxItem:
    """Wrap `envelope` as one InboxItem, classified by its payload's own type.

    Args:
        envelope: One envelope drained off an attached Warden's own link.
        principal: Who this item is from: the sending Warden's own id.

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


def human_inbox_item(message: HumanMessage, item_id: str, received_at: datetime) -> InboxItem:
    """Wrap a human's chat message as one InboxItem, from the `human` principal.

    Args:
        message: The wire form of the human's message, rebuilt from its chat line.
        item_id: The chat line's own id, so acting on the item can mark that line handled.
        received_at: When the line was appended: the item's age for the Attendant.

    Returns:
        A validated HUMAN_MESSAGE InboxItem, ready for `Attendant.order`.
    """
    kind, severity, task_id = _classify(message)
    return InboxItem(
        id=item_id,
        kind=kind,
        received_at=received_at,
        principal=HUMAN_PRINCIPAL,
        severity=severity,
        task_id=task_id,
        latency_budget_s=None,
        payload_kind=kind_for(HumanMessage),
        payload=message,
    )


def _classify(payload: object) -> tuple[InboxKind, AlarmSeverity | None, object | None]:
    """Return `(InboxKind, severity, task_id)` for `payload`'s own type.

    Unrecognised payload types fall back to `(WAGGLE_MESSAGE, None, None)` rather than raising:
    `hivemind.queen.autopilot.table.decide`'s own `NEEDS_JUDGEMENT` case is what a truly novel
    kind is for.
    """
    if isinstance(payload, AlarmRaised):
        alarm = Alarm.from_wire(payload)
        return InboxKind.ALARM, alarm.severity, payload.context.task_id
    if isinstance(payload, Question):
        return InboxKind.QUESTION, None, payload.task_id
    if isinstance(payload, HumanMessage):
        # Its own kind, never a routine Waggle message: the human is weighed heavily (8.8).
        return InboxKind.HUMAN_MESSAGE, None, payload.task_id
    if isinstance(payload, Answer | TaskResult | CellWaxProposed):
        # CellWaxProposed (roadmap step 4.2a) is deliberately not its own InboxKind: it scores
        # like any other routine Waggle message (WAGGLE_MESSAGE's base weight sits well below
        # ALARM/HUMAN_MESSAGE/QUESTION in WeightTable.queen_default), matching "a proposal is an
        # inbox item the Attendant scores low".
        return InboxKind.WAGGLE_MESSAGE, None, payload.task_id
    return InboxKind.WAGGLE_MESSAGE, None, None
