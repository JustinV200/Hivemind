"""Provide PushHumanChannel: the Queen's HumanChannel, told to the human's devices by push.

The Queen tells the human's end of her inbox (the ``HumanChannel`` seam, a no-op until now) that a
reply, a question, an Alarm or a goal's outcome is waiting. This is its Hive Entrance side (ADR-0032
and ADR-0034): each call becomes a content-free push notice queued on the ``PushOutbox``, and every
close (an answered question, an acknowledged Alarm, a settled request) becomes a withdrawal, which
reaches exactly the subscriptions that received the original. Who hears what is ``audience_for``'s
rule: a reply goes to devices that may read the chat, a question to devices that may answer, an
Alarm to every device, a finished goal to the device that submitted it alone. A goal request held
for the human's yes is a question in all but name ("do you want me to do this?"), so it is pushed
as ``question_waiting`` about the request and withdrawn once the request is planned or refused.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.notify``. Handed to the
    Queen as ``QueenDeps.human_channel`` by the ``hive serve`` composition root; called from her
    tick. Calls into the outbox only, so it never blocks her.

Key invariants:
    - Every method only queues and returns: nothing here awaits a delivery.
    - A notice's ref is an id (a chat line, a question, an Alarm, a goal request); no text leaves.

See Also:
    - hivemind.queen.chat.channel for the HumanChannel protocol and when the Queen calls it.
    - docs/adr/0034-landing-board-versioning-and-push.md for the notice kinds and audiences.
"""

from __future__ import annotations

from hivemind.brood_chamber import QuestionStatus
from hivemind.entrance.notify.outbox import PushOutbox
from hivemind.entrance.push import NoticeKind
from hivemind.queen import ChatEntry, GoalRequest

__all__ = ["PushHumanChannel"]


class PushHumanChannel:
    """The HumanChannel over the push outbox: each call queues a notice or a withdrawal."""

    def __init__(self, outbox: PushOutbox) -> None:
        """Build the channel.

        Args:
            outbox: Where every notice and withdrawal is queued.
        """
        self._outbox = outbox

    async def replied(self, entry: ChatEntry) -> None:
        """The Queen said something in the chat: ``reply_waiting`` about the line.

        Args:
            entry: Her REPLY or NOTICE line, already appended.
        """
        self._outbox.push(NoticeKind.REPLY_WAITING, entry.id)

    async def question_asked(self, entry: ChatEntry) -> None:
        """A question was routed to the human: ``question_waiting`` about the question.

        Args:
            entry: The QUESTION line; its ``ref`` is the question id the human answers by.
        """
        self._outbox.push(NoticeKind.QUESTION_WAITING, entry.ref or entry.id)

    async def question_closed(self, question_id: str, status: QuestionStatus) -> None:
        """A question was answered or withdrawn: withdraw it from every device that was told.

        Args:
            question_id: The question.
            status: ANSWERED or WITHDRAWN; both close it everywhere.
        """
        self._outbox.withdraw(question_id)

    async def alarm_raised(self, entry: ChatEntry) -> None:
        """An Alarm reached the human: ``alarm_waiting`` about the Alarm, to every device.

        Args:
            entry: The ALARM line; its ``ref`` is the Alarm id the human acknowledges.
        """
        self._outbox.push(NoticeKind.ALARM_WAITING, entry.ref or entry.id)

    async def alarm_acknowledged(self, alarm_id: str) -> None:
        """The human acknowledged an Alarm: withdraw it from every device that was told.

        Args:
            alarm_id: The Alarm.
        """
        self._outbox.withdraw(alarm_id)

    async def goal_request_held(self, request: GoalRequest) -> None:
        """A goal waits for the human's yes: ``question_waiting`` about the request.

        Args:
            request: The request, AWAITING_CONFIRMATION.
        """
        self._outbox.push(NoticeKind.QUESTION_WAITING, request.id)

    async def goal_request_planned(self, request: GoalRequest) -> None:
        """A request was planned: withdraw the confirmation it may have been waiting on.

        Args:
            request: The request, PLANNED.
        """
        self._outbox.withdraw(request.id)

    async def goal_request_refused(self, request: GoalRequest) -> None:
        """A request will never run: withdraw any wait, and tell its device the goal is over.

        Args:
            request: The request, REFUSED.
        """
        self._outbox.withdraw(request.id)
        # Refused is the goal's end as far as its device is concerned; it reads why itself.
        if request.device_id is not None:
            self._outbox.push(NoticeKind.GOAL_COMPLETED, request.id, submitter=request.device_id)

    async def goal_finished(self, request: GoalRequest) -> None:
        """Every task of a requested goal is terminal: ``goal_completed`` to its device alone.

        Args:
            request: The PLANNED request, its ``finished_at`` just set.
        """
        # The operator's local CLI has no device to tell; its own run watches the goal instead.
        if request.device_id is not None:
            self._outbox.push(NoticeKind.GOAL_COMPLETED, request.id, submitter=request.device_id)
