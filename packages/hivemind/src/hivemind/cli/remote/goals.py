"""Submit a goal to a remote Hive as an enrolled device, and follow it to its end.

``hive run --remote "goal"`` is a goal given to the Hive through its Entrance (the Hive's HTTP
door), not a Hive run in this process: ``POST /v1/goals`` commits it in the Queen's tables and
answers with the goal request's id, and the Queen plans it on her own tick. Following it needs no
polling loop of its own making (codingrules 8.15, "push, not polling"): the chat view
(``/v1/chat/stream``, from the chat's newest line before the submission) carries the Queen's
replies, notices, questions and Alarms as they are written, and the push view (``/v1/push/
stream``) says ``goal_completed`` for this request the moment it ends; either wakes a re-read of
``GET /v1/goals/{id}``, which is the one place the goal's end is recorded. A device may lack the
chat (no ``honey:clearance:c2``) or push (no ``entrance:push``): the follow then goes on without
that view, and a re-read at least every ``GOAL_RECHECK_S`` still sees the end. A view the Entrance
closes because this client fell behind is reopened from the last line seen; any other close ends
the follow with the reason. Printing is the caller's: ``Follower`` receives what arrives.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.remote``. Called by ``hive run --remote``
    (``hivemind.cli.remote.commands``). Calls into ``hivemind.cli.landing`` (signed calls and
    views) and the Landing Board's models.

Key invariants:
    - A goal is submitted once; everything after is reading.
    - The follow ends when the goal request is finished or refused, or at the caller's timeout;
      the goal itself goes on regardless.

See Also:
    - hivemind.entrance.routes.goals and hivemind.entrance.streams.views for the server half.
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.cli.landing import (
    CAPABILITY_CODE,
    FELL_BEHIND,
    FORBIDDEN,
    LandingRefusedError,
    SignedIn,
    View,
    ViewClosedError,
    open_view,
)
from hivemind.entrance.models import (
    ChatFrame,
    ChatLine,
    ChatPage,
    GoalAccepted,
    GoalSubmission,
    GoalView,
)
from hivemind.entrance.push import NoticeKind, PushNotice

GOAL_RECHECK_S = 10.0  # The longest the goal's state goes unread when no view says it moved.
VIEW_WAIT_S = 30.0  # One wait on a view; a quiet view stays open and is waited on again.
CHAT_FORBIDDEN_NOTE = (
    "This device may not read the chat (it needs honey:clearance:c2); following the goal's "
    "state only."
)

__all__ = [
    "CHAT_FORBIDDEN_NOTE",
    "GOAL_RECHECK_S",
    "FollowOutcome",
    "Follower",
    "GoalAsk",
    "follow_goal",
    "submit_and_follow",
]


class Follower(Protocol):
    """What a follow reports, as it happens; the command prints it."""

    def submitted(self, accepted: GoalAccepted) -> None:
        """The goal request was committed in the Queen's tables."""
        ...

    def line(self, line: ChatLine) -> None:
        """A chat line arrived after the submission (the Queen's, or another device's)."""
        ...

    def note(self, text: str) -> None:
        """Something about the follow itself (a view this device may not read)."""
        ...


@dataclass(frozen=True, slots=True)
class GoalAsk:
    """A goal as ``hive run --remote`` was given it.

    Attributes:
        text: The goal, in the human's words.
        clearance: Its data-sensitivity ceiling.
        comb_shield: The tier every task needs, or None to leave tiers to the planner.
    """

    text: str
    clearance: HoneyClearance
    comb_shield: CombShieldLevel | None


@dataclass(frozen=True, slots=True)
class FollowOutcome:
    """Where the goal request stood when the follow ended.

    Attributes:
        view: The request's last state as read.
        timed_out: The caller's timeout ended the follow before the goal did.
    """

    view: GoalView
    timed_out: bool


async def submit_and_follow(
    board: SignedIn, ask: GoalAsk, timeout_s: float, follower: Follower
) -> FollowOutcome:
    """Submit ``ask`` and follow it until it is finished or refused, or ``timeout_s`` passes.

    Args:
        board: The logged-in device.
        ask: The goal.
        timeout_s: The longest to follow, the submission included.
        follower: Told everything that arrives.

    Returns:
        Where the request stood.

    Raises:
        LandingRefusedError: The submission was refused (a held request names its pending
            confirmation), or a view closed for a reason other than falling behind.
    """
    # Read before submitting, so every line the goal causes is after this cursor.
    after = await _newest_seq(board)
    body = GoalSubmission(text=ask.text, comb_shield=ask.comb_shield, clearance=ask.clearance)
    accepted = await board.call("POST", "/v1/goals", body, GoalAccepted)
    follower.submitted(accepted)
    if after is None:
        follower.note(CHAT_FORBIDDEN_NOTE)
    return await follow_goal(board, accepted.id, after, timeout_s, follower)


async def follow_goal(
    board: SignedIn, request_id: str, after: int | None, timeout_s: float, follower: Follower
) -> FollowOutcome:
    """Follow one goal request until it is finished or refused, or ``timeout_s`` passes.

    Args:
        board: The logged-in device.
        request_id: The goal request (``goalreq_...``).
        after: The chat position to relay lines after; None follows without the chat.
        timeout_s: The longest to follow.
        follower: Told every line that arrives.

    Returns:
        Where the request stood.

    Raises:
        LandingError: A read was refused or a view closed for a reason other than falling behind.
    """
    try:
        # External wait: the Hive's own work, bounded by the caller's patience.
        async with asyncio.timeout(timeout_s):
            view = await _race(board, request_id, after, follower)
    except TimeoutError:
        # The goal goes on without this terminal; say where it stood.
        return FollowOutcome(await _read_goal(board, request_id), timed_out=True)
    except BaseExceptionGroup as group:
        # A view's own failure, not the group it travelled in, is what the operator reads.
        raise _first_failure(group) from group
    return FollowOutcome(view, timed_out=False)


async def _race(
    board: SignedIn, request_id: str, after: int | None, follower: Follower
) -> GoalView:
    """Watch both views while re-reading the goal until it ends; stop the views then."""
    moved = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        watchers = [group.create_task(_watch_push(board, request_id, moved))]
        if after is not None:
            watchers.append(group.create_task(_relay_chat(board, after, follower, moved)))
        try:
            return await _until_done(board, request_id, moved)
        finally:
            for watcher in watchers:
                watcher.cancel()


async def _until_done(board: SignedIn, request_id: str, moved: asyncio.Event) -> GoalView:
    """Re-read the goal whenever a view says it moved, and at least every GOAL_RECHECK_S."""
    while True:
        # Cleared before the read, so a push that lands during it wakes the next wait at once.
        moved.clear()
        view = await _read_goal(board, request_id)
        if view.finished_at is not None or view.refused:
            return view
        try:
            async with asyncio.timeout(GOAL_RECHECK_S):
                await moved.wait()
        except TimeoutError:
            continue


async def _relay_chat(
    board: SignedIn, after: int, follower: Follower, moved: asyncio.Event
) -> None:
    """Relay every chat line after ``after``, reopening the view whenever it fell behind."""
    cursor: int | None = after
    while cursor is not None:
        cursor = await _relay_once(board, cursor, follower, moved)


async def _relay_once(
    board: SignedIn, cursor: int, follower: Follower, moved: asyncio.Event
) -> int | None:
    """Relay lines until the view closes; the cursor to reopen from, or None to stop."""
    try:
        async with open_view(board, f"/v1/chat/stream?after={cursor}") as view:
            while True:
                frame = await _next(view, ChatFrame)
                if frame is not None:
                    cursor = frame.entry.seq
                    follower.line(frame.entry)
                    moved.set()
    except ViewClosedError as closed:
        if closed.close_code == FELL_BEHIND:
            return cursor
        if closed.close_code == FORBIDDEN:
            follower.note(CHAT_FORBIDDEN_NOTE)
            return None
        raise


async def _watch_push(board: SignedIn, request_id: str, moved: asyncio.Event) -> None:
    """Wake a re-read when the push view says this goal request completed."""
    while True:
        try:
            async with open_view(board, "/v1/push/stream") as view:
                while True:
                    notice = await _next(view, PushNotice)
                    if notice is not None and _completes(notice, request_id):
                        moved.set()
        except ViewClosedError as closed:
            # Without entrance:push the recheck interval alone sees the end; fine, not fatal.
            if closed.close_code == FORBIDDEN:
                return
            if closed.close_code != FELL_BEHIND:
                raise


def _completes(notice: PushNotice, request_id: str) -> bool:
    """Whether ``notice`` says this goal request ended (finished, or refused)."""
    return notice.kind is NoticeKind.GOAL_COMPLETED and notice.ref == request_id


async def _next[ModelT: BaseModel](view: View, model: type[ModelT]) -> ModelT | None:
    """The view's next frame, or None when it stayed quiet for VIEW_WAIT_S."""
    try:
        return await view.next(model, VIEW_WAIT_S)
    except TimeoutError:
        return None


async def _read_goal(board: SignedIn, request_id: str) -> GoalView:
    """Read the goal request's state (never its text)."""
    return await board.call("GET", f"/v1/goals/{request_id}", None, GoalView)


async def _newest_seq(board: SignedIn) -> int | None:
    """The chat's newest position (0 when empty), or None when this device may not read it."""
    try:
        page = await board.call("GET", "/v1/chat?limit=1", None, ChatPage)
    except LandingRefusedError as refusal:
        # Reading the chat is C2: a device without that clearance follows the state alone.
        if refusal.body.error == CAPABILITY_CODE:
            return None
        raise
    return page.newest_seq or 0


def _first_failure(group: BaseExceptionGroup[BaseException]) -> BaseException:
    """The first leaf failure inside ``group`` (nested groups unwrapped)."""
    first = group.exceptions[0]
    if isinstance(first, BaseExceptionGroup):
        return _first_failure(first)
    return first
