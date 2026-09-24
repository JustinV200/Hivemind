"""Define ChatDoor: the Queen's human-facing methods, the ones the Hive Entrance calls.

Docs/adr/0032: every write the Entrance makes goes to the Queen (codingrules 8.11), and the chat
is the human's way to request tasks, ask and answer (README, "Chat"). `ChatDoor` is that door, a
mixin `hivemind.queen.queen.Queen` inherits so her own class stays within codingrules 5.1's size
limits (the same composition `hivemind.brood_chamber.BroodChamber` uses): `request_goal` commits a
durable goal request and wakes her, `confirm_goal_request`/`decline_goal_request` settle one held
for the human's yes, `post_human_message` appends the human's words to the chat and wakes her,
`acknowledge_alarm` resolves an Alarm that reached the human, `escalate_to_human` puts an Alarm the
Hive itself raised (the Entrance's remote listener failing) in front of the human, and
`cancel_goal` cancels what of a goal has not started yet (a revoked device's goals). Each one is a
thin delegate to `hivemind.queen.intake.writes` or `hivemind.queen.chat.post`, or one chamber edge;
waking her is the in-process signal she awaits beside her Warden links (`QueenDeps.wake`), so a
request or a message is acted on at once rather than at the next Heartbeat.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's chat
    sub-package. Inherited by `Queen`; called by the Hive Entrance (a later step) and by tests.
    Calls into `hivemind.queen.chat.post`, `hivemind.queen.intake` and waggle only; `QueenDeps`
    and `HumanInbox` only for their types.

Key invariants:
    - `request_goal` returns only once the row is committed: the Entrance answers `202` after it.
    - `cancel_goal` cancels only PENDING tasks (never dispatched), through the chamber's own
      cancel edge; work already on a Warden runs to its end, and the goal is reported running.
    - Nothing here plans a goal or runs a model: the Queen's own tick does, so reducing the
      Entrance (or a request handler failing) can never kill planning.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the decision.
    - hivemind.queen.ticks.intake and hivemind.queen.ticks.chat for what the wake leads to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.brood_chamber import InvalidTransitionError, TaskFilter, TaskStatus, is_terminal
from hivemind.queen.chat.model import ChatEntryId
from hivemind.queen.chat.post import escalate_alarm, post_message, resolve_alarm
from hivemind.queen.intake import GoalRequest, GoalRequestId, confirm, decline, receive
from hivemind.supervision import Alarm
from waggle.ids import DeviceId, TaskId

if TYPE_CHECKING:
    # Only for the two attribute annotations below: both are set by Queen.__init__, and a real
    # import of hivemind.queen.deps here would cycle back through this package.
    from hivemind.queen.deps import QueenDeps
    from hivemind.queen.human_inbox import HumanInbox

__all__ = ["ChatDoor"]


class ChatDoor:
    """The Queen's human-facing methods, mixed into `Queen` (module docstring).

    Reads `self._deps` and `self._human_inbox`, both set by `Queen.__init__`; this class is never
    instantiated on its own. The annotations declare that shared state for mypy --strict.
    """

    _deps: QueenDeps
    _human_inbox: HumanInbox

    async def request_goal(self, request: GoalRequest) -> GoalRequestId:
        """Commit a goal request durably, then wake the Queen to plan it (ADR-0032).

        Args:
            request: A fresh request, RECEIVED, built by the Hive Entrance from a device's body.

        Returns:
            Its id, once its row and `queen.goal_request_received` event are committed.

        Raises:
            InvalidGoalRequestTransitionError: `request` is not a fresh RECEIVED request.
            GoalRequestExistsError: Its id is already taken; nothing was written.
        """
        await receive(self._deps, request)
        self._deps.wake.set()
        return request.id

    async def confirm_goal_request(self, request_id: str) -> GoalRequest:
        """Confirm a goal request held for the human's yes, then wake the Queen to plan it.

        Args:
            request_id: The request the human confirmed (its echo's `ref` in the chat).

        Returns:
            The request, RECEIVED again with `confirmed_at` set.

        Raises:
            GoalRequestNotFoundError: No such request exists.
            InvalidGoalRequestTransitionError: It is not awaiting confirmation.
        """
        confirmed = await confirm(self._deps, request_id)
        self._deps.wake.set()
        return confirmed

    async def decline_goal_request(self, request_id: str, reason: str) -> GoalRequest:
        """Refuse a goal request held for the human's yes: the human said no.

        Args:
            request_id: The request the human declined.
            reason: Why, kept on the row for the human (never on the trail).

        Returns:
            The request, REFUSED.

        Raises:
            GoalRequestNotFoundError: No such request exists.
            InvalidGoalRequestTransitionError: It is not awaiting confirmation.
        """
        declined = await decline(self._deps, request_id, reason)
        await self._deps.human_channel.goal_request_refused(declined)
        return declined

    async def post_human_message(
        self, text: str, device_id: DeviceId, task_id: TaskId | None = None
    ) -> ChatEntryId:
        """Append the human's message to the chat, then wake the Queen to read it.

        The message enters her inbox on the tick this wakes, as a `HumanMessage` her Attendant
        scores; autopilot has no rule for free text, so an awake episode decides what to do.

        Args:
            text: The human's own words.
            device_id: The enrolled device they wrote from.
            task_id: The task the message concerns; None addresses the Queen at large.

        Returns:
            The chat line's id, once it and its `queen.human_message_received` event are committed.

        Raises:
            pydantic.ValidationError: `text` is empty or longer than the chat's own bound.
        """
        entry = await post_message(self._deps, text, device_id, task_id)
        self._deps.wake.set()
        return entry.id

    async def acknowledge_alarm(self, alarm_id: str) -> bool:
        """Resolve an Alarm that reached the human, now that the human has acknowledged it.

        Args:
            alarm_id: The Alarm (an ALARM chat line's `ref`).

        Returns:
            True when it was waiting on the human and is now resolved; False when nothing was.
        """
        return await resolve_alarm(self._deps, self._human_inbox, alarm_id)

    async def escalate_to_human(self, alarm: Alarm) -> None:
        """Put an Alarm the Hive itself raised in front of the human, as the chain's last hop.

        Args:
            alarm: The Alarm, HANDLING; e.g. the Hive Entrance's remote listener failed and the
                Entrance was reduced to loopback only (ADR-0033).
        """
        await escalate_alarm(self._deps, self._human_inbox, alarm)

    async def cancel_goal(self, goal_id: TaskId, reason: str) -> bool:
        """Cancel every task of a goal that has not started yet, and wake the Queen.

        Args:
            goal_id: The goal (its first task's id), e.g. one a revoked device submitted.
            reason: A short phrase each `task.cancelled` event records.

        Returns:
            True when no task of the goal is left unfinished; False while some are still on a
            Warden (they run to their end: no Warden-side cancel exists yet).
        """
        # Latency: one local chamber read, then one local transaction per task cancelled.
        tasks = await self._deps.chamber.list(TaskFilter(goal_id=goal_id))
        for task in tasks:
            if task.status is not TaskStatus.PENDING:
                continue
            try:
                await self._deps.chamber.cancel(task.id, reason)
            except InvalidTransitionError:
                # Dispatched between the read and the cancel: it runs to its end.
                continue
        self._deps.wake.set()
        remaining = await self._deps.chamber.list(TaskFilter(goal_id=goal_id))
        return all(is_terminal(task.status) for task in remaining)
