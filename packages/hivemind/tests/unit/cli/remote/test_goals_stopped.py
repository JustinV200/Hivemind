"""Test hivemind.cli.remote.goals when the Hive goes away mid-follow: stopped, never refused.

A follow is a live conversation with the Hive Stand's Entrance: two views over WebSockets and a
re-read of the goal over HTTP. When `hive serve` shuts down, every view is closed with 1001 (the
Entrance's own SHUTTING_DOWN), its server's own shutdown may close one with 1012, a dropped
connection closes one with no close frame at all, and a read finds nothing listening. Each of
those is the Hive going away, and the follow ends with `HiveStoppedError`, carrying where the goal
stood when last read; a close that refuses this device (4401, 4410) is still a refusal. These
tests script the Entrance's answers in process, one close cause at a time.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/goals.py (codingrules section 3), split by feature from
    test_goals.py, which follows real goals over a real Entrance.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from hivemind.cell import HoneyClearance
from hivemind.cli.landing import (
    FORBIDDEN,
    GOING_AWAY,
    SERVICE_RESTART,
    EntranceUnreachableError,
    SignedIn,
    ViewClosedError,
)
from hivemind.cli.remote import FollowPace, HiveStoppedError, follow_goal
from hivemind.cli.remote import goals as goals_module
from hivemind.entrance.models import ChatLine, GoalAccepted, GoalView
from hivemind.queen import GoalRequestState, GoalSource

_AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
_REQUEST = "goalreq_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3"
_PACE = FollowPace(timeout_s=5.0, recheck_s=0.01)  # Re-read quickly; nothing here waits long.
_AUTHENTICATION_FAILED = 4401  # CloseReason.AUTHENTICATION_FAILED: no valid first frame.
_SESSION_ENDED = 4410  # CloseReason.SESSION_ENDED: logout, expiry, lock or revocation.


def _view(*, done: bool = False) -> GoalView:
    """The goal request as the Entrance reports it: planned, finished when `done`."""
    return GoalView(
        id=_REQUEST,
        state=GoalRequestState.PLANNED,
        goal_id="task_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        budget_usd=None,
        comb_shield=None,
        clearance=HoneyClearance.C1,
        source=GoalSource.TYPED,
        needs_confirmation=False,
        refused=False,
        received_at=_AT,
        updated_at=_AT,
        confirmed_at=None,
        finished_at=_AT if done else None,
    )


class _Follower:
    """A Follower that keeps nothing: these tests read how the follow ends, not what it relayed."""

    def submitted(self, accepted: GoalAccepted) -> None:
        """Ignore the acceptance."""

    def line(self, line: ChatLine) -> None:
        """Ignore a line."""

    def note(self, text: str) -> None:
        """Ignore a note."""


class _Board(SignedIn):
    """A logged-in device whose reads the test scripts; it opens no socket of its own.

    `open_view` is replaced in every test here, so nothing reaches a client or a session, and
    `SignedIn.__init__` (which only keeps those) is not needed.
    """

    def __init__(self, view: GoalView, answers: int = 1_000) -> None:
        """Answer `answers` reads, then find nothing listening at all.

        Args:
            view: What every answered goal read says.
            answers: How many reads (goal or chat) are answered before the Entrance is gone.
        """
        self._view = view
        self._answers = answers

    async def call[ModelT: BaseModel](
        self, method: str, target: str, body: BaseModel | None, model: type[ModelT]
    ) -> ModelT:
        """Answer a goal read or a chat page (empty), or say nothing answered."""
        if self._answers <= 0:
            raise EntranceUnreachableError(
                "The Entrance at https://stand.test did not answer (ConnectError: refused)."
            )
        self._answers -= 1
        if target.startswith("/v1/chat"):
            return model.model_validate({"entries": [], "newest_seq": None})
        return model.model_validate(self._view.model_dump())


class _ClosedView:
    """A view the Entrance closes as soon as it is read, with `close_code`."""

    def __init__(self, target: str, close_code: int | None) -> None:
        """Close `target` with `close_code` (None: no close frame, the connection dropped)."""
        self._target = target
        self._close_code = close_code

    async def next(self, model: type[BaseModel], timeout_s: float) -> BaseModel:
        """Raise the close, as View.next does for a socket the Entrance closed."""
        raise ViewClosedError(self._target, self._close_code, "")


def _views_closed_with(monkeypatch: pytest.MonkeyPatch, close_code: int | None) -> None:
    """Have every view the follow opens closed by the Entrance with `close_code`."""

    @asynccontextmanager
    async def closing(_board: SignedIn, target: str) -> AsyncIterator[_ClosedView]:
        """Open nothing; yield a view that is closed the moment it is read."""
        yield _ClosedView(target, close_code)

    monkeypatch.setattr(goals_module, "open_view", closing)


@pytest.mark.parametrize(
    ("close_code", "shut_down"),
    [(GOING_AWAY, True), (SERVICE_RESTART, True), (None, False)],
)
async def test_a_hive_that_goes_away_mid_goal_is_said_to_have_stopped(
    monkeypatch: pytest.MonkeyPatch, close_code: int | None, shut_down: bool
) -> None:
    # 1001: hive serve's own shutdown; 1012: its server going down; None: the connection dropped.
    _views_closed_with(monkeypatch, close_code)

    with pytest.raises(HiveStoppedError) as stopped:
        await follow_goal(_Board(_view()), _REQUEST, 0, _PACE, _Follower())

    assert stopped.value.request_id == _REQUEST
    assert stopped.value.shut_down is shut_down
    assert stopped.value.last_view == _view()  # Where it stood when last read: still running.
    assert "refused" not in str(stopped.value)


@pytest.mark.parametrize("close_code", [_AUTHENTICATION_FAILED, _SESSION_ENDED])
async def test_a_view_closed_on_this_device_is_still_its_refusal(
    monkeypatch: pytest.MonkeyPatch, close_code: int
) -> None:
    _views_closed_with(monkeypatch, close_code)

    with pytest.raises(ViewClosedError) as refused:
        await follow_goal(_Board(_view()), _REQUEST, 0, _PACE, _Follower())

    assert refused.value.close_code == close_code
    assert not refused.value.went_away


async def test_a_goal_read_that_finds_nothing_listening_is_the_hive_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A device with neither view: only its own re-reads see the Hive go, and one finds nothing.
    _views_closed_with(monkeypatch, FORBIDDEN)

    with pytest.raises(HiveStoppedError) as stopped:
        await follow_goal(_Board(_view(), answers=1), _REQUEST, 0, _PACE, _Follower())

    assert stopped.value.shut_down is False
    assert stopped.value.last_view == _view()


async def test_a_goal_that_ended_before_the_hive_went_is_its_own_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The end was read first; the closing catch-up then finds nothing listening, and that is fine.
    _views_closed_with(monkeypatch, FORBIDDEN)

    outcome = await follow_goal(
        _Board(_view(done=True), answers=1), _REQUEST, 0, _PACE, _Follower()
    )

    assert outcome.view.finished_at is not None and not outcome.timed_out


async def test_a_hive_gone_by_the_follows_timeout_is_said_to_have_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One read, then quiet until the timeout; the last read, after it, finds nothing listening.
    _views_closed_with(monkeypatch, FORBIDDEN)
    pace = FollowPace(timeout_s=0.05, recheck_s=30.0)

    with pytest.raises(HiveStoppedError) as stopped:
        await follow_goal(_Board(_view(), answers=1), _REQUEST, 0, pace, _Follower())

    assert stopped.value.shut_down is False
    assert stopped.value.last_view == _view()
