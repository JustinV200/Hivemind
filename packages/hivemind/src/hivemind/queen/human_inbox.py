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
docstring), and this class stays a plain, dependency-free container.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Owned by
    one `hivemind.queen.queen.Queen` instance; read by `hive inbox` (roadmap step 3.21) through
    whatever the composition root exposes. Calls into `hivemind.brood_chamber` (BroodChamber,
    Question) and `hivemind.supervision` (Alarm) only.

Key invariants:
    - `alarms` never grows unbounded on its own: `resolve` is the one way an entry leaves it, and
      `hivemind.queen.ticks.alarms` is the only caller of `add_alarm` this phase.
    - `pending_questions` never duplicates the Brood Chamber's own state: it is a read-through, not
      a cache, so a question answered elsewhere (`hivemind.queen.questions.answer_question`) is
      never stale here.

See Also:
    - .claude/codingrules.md section 8.8 for "the Queen alone decides when a question goes to the
      human" and the escalation chain's last hop.
    - hivemind.brood_chamber for BroodChamber and Question, what `pending_questions` reads.
    - hivemind.supervision for Alarm, the value `alarms` holds.
    - hivemind.queen.ticks.alarms for the one caller of add_alarm.
"""

from __future__ import annotations

from hivemind.brood_chamber import BroodChamber, Question
from hivemind.supervision import Alarm
from waggle.ids import AlarmId

__all__ = ["HumanInbox"]


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
