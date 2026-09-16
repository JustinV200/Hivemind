"""Define HumanInbox: pending questions and Alarms waiting on the human, read through the Queen.

Codingrules section 8.8: "The Queen alone decides when a question goes to the human," and every
Alarm the Queen cannot resolve herself escalates to the human as the last hop of the chain
(sub-bee -> Warden -> Queen -> human). `HumanInbox` is the one place both of those become visible:
`pending_questions` is a thin pass-through to the Brood Chamber's own `pending_questions` (every
`BLOCK_ON_QUESTION` decision already wrote its `Question` there through `chamber.ask`, so this
class holds no question state of its own); `alarms` is a bare in-memory list of the `Alarm` values
`hivemind.queen.ticks.alarms`' `ESCALATE_TO_HUMAN` handler adds, since the Brood Chamber has no
Alarm table of its own to read back (codingrules Appendix C: an Alarm's durable home this phase is
the trail, not a store `HumanInbox` can query). `add_alarm`/`resolve` never write a trail event
themselves -- codingrules section 12's `alarm.escalated` is the *supervision* owner's event, so a
caller of `add_alarm` records `queen.decided` instead (`hivemind.queen.ticks.alarms`'s own
docstring), and this class stays a plain, dependency-free container. `propose_wax_from_chat`
(roadmap step 4.2a) is the smallest hook for the human's own half of "any bee, Warden or the human
may propose one, over Waggle... or from the chat": it builds the same wire `CellWaxProposed`
shape a Warden's own `hivemind.wardens.requests.propose_wax` builds, with `origin=HUMAN` and
`proposer=None`, so `hivemind.queen.ticks.wax.handle_wax_proposed` runs the exact same rule for
either -- there is no second "human wax" code path to keep in sync. It builds the message only;
a future Entrance chat route (outside this dispatch's own files) is what would call it and hand
the result to `handle_wax_proposed`.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Owned by
    one `hivemind.queen.queen.Queen` instance; read by `hive inbox` (roadmap step 3.21) through
    whatever the composition root exposes. Calls into `hivemind.brood_chamber` (BroodChamber,
    Question), `hivemind.supervision` (Alarm) and `waggle.messages.cell` (CellWaxProposed,
    WaxOrigin, WaxSeverity) only.

Key invariants:
    - `alarms` never grows unbounded on its own: `resolve` is the one way an entry leaves it, and
      `hivemind.queen.ticks.alarms` is the only caller of `add_alarm` this phase.
    - `pending_questions` never duplicates the Brood Chamber's own state: it is a read-through, not
      a cache, so a question answered elsewhere (`hivemind.queen.questions.answer_question`) is
      never stale here.
    - `propose_wax_from_chat` always sets `origin=WaxOrigin.HUMAN` and `proposer=None`, the one
      combination `CellWaxProposed`'s own validator requires for a human proposal.

See Also:
    - .claude/codingrules.md section 8.8 for "the Queen alone decides when a question goes to the
      human" and the escalation chain's last hop.
    - .claude/roadmap.md step 4.2a for "any bee, Warden or the human may propose one... or from
      the chat".
    - hivemind.brood_chamber for BroodChamber and Question, what `pending_questions` reads.
    - hivemind.supervision for Alarm, the value `alarms` holds.
    - hivemind.queen.ticks.alarms for the one caller of add_alarm.
    - hivemind.queen.ticks.wax for handle_wax_proposed, the one rule every proposal (chat included)
      is judged by.
    - hivemind.wardens.requests for propose_wax, the Warden-side sibling builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hivemind.brood_chamber import BroodChamber, Question
from hivemind.supervision import Alarm
from waggle.ids import AlarmId, CellId
from waggle.messages.cell import CellWaxProposed
from waggle.messages.cell.wax import WaxOrigin, WaxSeverity
from waggle.messages.labels import HoneyClearance

__all__ = ["ChatWaxProposal", "HumanInbox", "propose_wax_from_chat"]


class HumanInbox:
    """Pending questions (read through the chamber) and Alarms (held in memory) awaiting a human.

    Owns its own mutable state in place (codingrules section 8.5): `_alarms` grows on `add_alarm`
    and shrinks on `resolve`.
    """

    def __init__(self) -> None:
        """Create a HumanInbox with no Alarms escalated yet."""
        self._alarms: dict[AlarmId, Alarm] = {}

    async def pending_questions(self, chamber: BroodChamber) -> tuple[Question, ...]:
        """Return every question still awaiting an answer, read straight from the Brood Chamber.

        Args:
            chamber: The Brood Chamber every `BLOCK_ON_QUESTION` decision already wrote to.

        Returns:
            Every `Question` with `status == QuestionStatus.ASKED`, ordered by `(asked_at, id)`.
        """
        return await chamber.pending_questions()

    @property
    def alarms(self) -> tuple[Alarm, ...]:
        """Every Alarm currently escalated to the human, in no particular order."""
        return tuple(self._alarms.values())

    def add_alarm(self, alarm: Alarm) -> None:
        """Escalate `alarm` to the human.

        Args:
            alarm: The Alarm to add; a repeat `add_alarm` for the same `alarm.id` replaces the
                prior entry, so a re-escalated Alarm (a later attempt, same id) never duplicates.
        """
        self._alarms[alarm.id] = alarm

    def resolve(self, alarm_id: AlarmId) -> None:
        """Remove an Alarm once it is resolved.

        Idempotent: resolving an id not currently escalated is a no-op, not an error.

        Args:
            alarm_id: The Alarm to remove.
        """
        self._alarms.pop(alarm_id, None)


@dataclass(frozen=True, slots=True)
class ChatWaxProposal:
    """What the human typed in the chat about one Cell, before it becomes a CellWaxProposed.

    Attributes:
        cell_id: The Cell the caution is about.
        severity: NOTE, CAUTION or BLOCK.
        text: The caution itself.
        reason: Why the human believes it.
        clearance: The note's own data-sensitivity label; describing the operator's own machine
            is always at least C2 (codingrules section 6.1), a rule this dataclass does not
            enforce itself -- the caller states the label it already knows is correct.
        expires_at: When it expires on its own; None for standing wax.
    """

    cell_id: CellId
    severity: WaxSeverity
    text: str
    reason: str
    clearance: HoneyClearance
    expires_at: datetime | None = None


def propose_wax_from_chat(proposal: ChatWaxProposal) -> CellWaxProposed:
    """Build a CellWaxProposed for a caution the human typed in the chat (roadmap step 4.2a).

    Args:
        proposal: The caution's own content, as the human stated it.

    Returns:
        A validated CellWaxProposed with `origin=HUMAN` and `proposer=None`, ready for
        `hivemind.queen.ticks.wax.handle_wax_proposed`.
    """
    return CellWaxProposed(
        cell_id=proposal.cell_id,
        severity=proposal.severity,
        text=proposal.text,
        reason=proposal.reason,
        clearance=proposal.clearance,
        expires_at=proposal.expires_at,
        origin=WaxOrigin.HUMAN,
        proposer=None,
        task_id=None,
    )
