"""Define HumanChannel: the seam through which the Queen tells the human something is waiting.

The chat log (`hivemind.queen.chat.protocol.ChatLog`) is durable, and the Hive Entrance's
`/v1/chat` stream serves it, but a human who is not looking needs to be told: roadmap step 10.5b
pushes "a question for the human, an Alarm that reached the human, a reply from the Queen,
completion of a goal the device submitted" to every enrolled device, content-free. The Queen never
imports the Entrance (it sits a layer above her), so she calls this protocol instead, at each of
those moments; the Entrance implements it in a later step and is injected through
`QueenDeps.human_channel`. `NullHumanChannel` is the documented default: it tells nobody, which is
exactly right for `hive run` and every test that does not care, and it keeps the Queen's own code
free of "is anyone listening" branches.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package. Called by `hivemind.queen.chat.post` (a reply, a question, an Alarm, an
    acknowledgement), `hivemind.queen.questions` (a question answered) and the intake drain
    (`hivemind.queen.ticks.intake`: a request held, planned or refused; a goal finished).
    Implemented by the Hive Entrance's push layer. Calls into `hivemind.brood_chamber`
    (QuestionStatus), `hivemind.queen.chat.model` and `hivemind.queen.intake` only.

Key invariants:
    - Every method returns promptly and never raises: an implementation queues its notice and
      reports its own delivery failures, because the Queen calls it from inside her tick and a
      slow or failing push must never stall or stop her.
    - Nothing here carries more than the Queen already stored: the chat line or the request row
      itself, so an implementation decides what (if anything) leaves the Hive Stand.

See Also:
    - .claude/roadmap.md step 10.5b for the push channel that implements this protocol.
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the chat.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.brood_chamber import QuestionStatus
from hivemind.queen.chat.model import ChatEntry
from hivemind.queen.intake import GoalRequest

__all__ = ["HumanChannel", "NullHumanChannel"]


class HumanChannel(Protocol):
    """Tell the human's devices that something in the chat or a goal is waiting for them."""

    async def replied(self, entry: ChatEntry) -> None:
        """The Queen said something to the human: a reply, or a notice standing in for one.

        Args:
            entry: The Queen's chat line (kind REPLY or NOTICE), already appended.
        """
        ...

    async def question_asked(self, entry: ChatEntry) -> None:
        """A question was routed to the human.

        Args:
            entry: The QUESTION chat line; `ref` is the question id the human answers by.
        """
        ...

    async def question_closed(self, question_id: str, status: QuestionStatus) -> None:
        """A question the human was asked was answered or withdrawn: withdraw it everywhere.

        Args:
            question_id: The Brood Chamber's question id (the chat line's `ref`).
            status: ANSWERED or WITHDRAWN.
        """
        ...

    async def alarm_raised(self, entry: ChatEntry) -> None:
        """An Alarm reached the human, the escalation chain's last hop.

        Args:
            entry: The ALARM chat line; `ref` is the Alarm id the human acknowledges by.
        """
        ...

    async def alarm_acknowledged(self, alarm_id: str) -> None:
        """The human acknowledged an Alarm: withdraw it everywhere.

        Args:
            alarm_id: The Alarm the human acknowledged.
        """
        ...

    async def goal_request_held(self, request: GoalRequest) -> None:
        """A goal request waits for the human's confirmation (a spoken goal echoed back).

        Args:
            request: The request, AWAITING_CONFIRMATION.
        """
        ...

    async def goal_request_planned(self, request: GoalRequest) -> None:
        """A goal request was planned: its goal exists and is being placed.

        Args:
            request: The request, PLANNED, with its `goal_id`.
        """
        ...

    async def goal_request_refused(self, request: GoalRequest) -> None:
        """A goal request will never be planned.

        Args:
            request: The request, REFUSED, with its `refusal`.
        """
        ...

    async def goal_finished(self, request: GoalRequest) -> None:
        """Every task of a requested goal reached a terminal status.

        Args:
            request: The PLANNED request, its `finished_at` just set; `goal_id` names the goal.
        """
        ...


class NullHumanChannel:
    """The default HumanChannel: every call returns at once and tells nobody (module docstring)."""

    async def replied(self, entry: ChatEntry) -> None:
        """Tell nobody; see HumanChannel.replied."""

    async def question_asked(self, entry: ChatEntry) -> None:
        """Tell nobody; see HumanChannel.question_asked."""

    async def question_closed(self, question_id: str, status: QuestionStatus) -> None:
        """Tell nobody; see HumanChannel.question_closed."""

    async def alarm_raised(self, entry: ChatEntry) -> None:
        """Tell nobody; see HumanChannel.alarm_raised."""

    async def alarm_acknowledged(self, alarm_id: str) -> None:
        """Tell nobody; see HumanChannel.alarm_acknowledged."""

    async def goal_request_held(self, request: GoalRequest) -> None:
        """Tell nobody; see HumanChannel.goal_request_held."""

    async def goal_request_planned(self, request: GoalRequest) -> None:
        """Tell nobody; see HumanChannel.goal_request_planned."""

    async def goal_request_refused(self, request: GoalRequest) -> None:
        """Tell nobody; see HumanChannel.goal_request_refused."""

    async def goal_finished(self, request: GoalRequest) -> None:
        """Tell nobody; see HumanChannel.goal_finished."""
