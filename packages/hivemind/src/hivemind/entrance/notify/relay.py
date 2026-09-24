"""Provide HumanChannelRelay: the Queen's HumanChannel before and after the Entrance is built.

``hive serve`` builds the Queen first (the Hive ``hive run`` builds, synchronously), and the Hive
Entrance's push side only inside the running event loop, where its tables open. The Queen is
therefore handed this relay as her ``HumanChannel``, and the composition root points it at the
Entrance's ``PushHumanChannel`` once that exists, before her first tick. A call that arrives with no
target is dropped, which loses nothing: the chat and the trail hold everything a notice points at.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.notify``. Built and bound
    by the ``hive serve`` composition root; called by the Queen. Calls into its target only.

Key invariants:
    - Every call is forwarded unchanged to the target, or dropped when there is none yet.

See Also:
    - hivemind.queen.chat.channel for the HumanChannel protocol.
"""

from __future__ import annotations

from hivemind.brood_chamber import QuestionStatus
from hivemind.common.logging import get_logger
from hivemind.queen import ChatEntry, GoalRequest, HumanChannel

log = get_logger(__name__)

__all__ = ["HumanChannelRelay"]


class HumanChannelRelay:
    """A HumanChannel that forwards to a target set later; a no-op until then."""

    def __init__(self) -> None:
        """Start with no target."""
        self._target: HumanChannel | None = None

    def bind(self, target: HumanChannel) -> None:
        """Forward every call to ``target`` from now on.

        Args:
            target: The Entrance's channel (``PushHumanChannel``).
        """
        self._target = target

    async def replied(self, entry: ChatEntry) -> None:
        """Forward; see HumanChannel.replied."""
        if self._forwarding("replied"):
            await self._require().replied(entry)

    async def question_asked(self, entry: ChatEntry) -> None:
        """Forward; see HumanChannel.question_asked."""
        if self._forwarding("question_asked"):
            await self._require().question_asked(entry)

    async def question_closed(self, question_id: str, status: QuestionStatus) -> None:
        """Forward; see HumanChannel.question_closed."""
        if self._forwarding("question_closed"):
            await self._require().question_closed(question_id, status)

    async def alarm_raised(self, entry: ChatEntry) -> None:
        """Forward; see HumanChannel.alarm_raised."""
        if self._forwarding("alarm_raised"):
            await self._require().alarm_raised(entry)

    async def alarm_acknowledged(self, alarm_id: str) -> None:
        """Forward; see HumanChannel.alarm_acknowledged."""
        if self._forwarding("alarm_acknowledged"):
            await self._require().alarm_acknowledged(alarm_id)

    async def goal_request_held(self, request: GoalRequest) -> None:
        """Forward; see HumanChannel.goal_request_held."""
        if self._forwarding("goal_request_held"):
            await self._require().goal_request_held(request)

    async def goal_request_planned(self, request: GoalRequest) -> None:
        """Forward; see HumanChannel.goal_request_planned."""
        if self._forwarding("goal_request_planned"):
            await self._require().goal_request_planned(request)

    async def goal_request_refused(self, request: GoalRequest) -> None:
        """Forward; see HumanChannel.goal_request_refused."""
        if self._forwarding("goal_request_refused"):
            await self._require().goal_request_refused(request)

    async def goal_finished(self, request: GoalRequest) -> None:
        """Forward; see HumanChannel.goal_finished."""
        if self._forwarding("goal_finished"):
            await self._require().goal_finished(request)

    def _forwarding(self, call: str) -> bool:
        """Say whether a target exists; a call before one is dropped with a debug line."""
        if self._target is None:
            log.debug("entrance.human_channel_unbound", call=call)
            return False
        return True

    def _require(self) -> HumanChannel:
        """Return the target ``_forwarding`` just saw."""
        if self._target is None:
            raise RuntimeError("The human channel relay lost its target.")
        return self._target
