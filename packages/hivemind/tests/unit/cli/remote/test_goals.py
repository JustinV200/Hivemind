"""Test hivemind.cli.remote.goals: a goal given through the Entrance, followed to its real end.

``hive serve``'s own composition runs a real Queen over a scripted provider (``builders.entrance.
goals``): she plans the goal, a Drone on the Hive Stand's Cell writes a file (asking the human
first, in one scenario), the judge approves, and the goal finishes. The laptop's device follows it
through the chat and push views, and without them through its own re-reads.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/goals.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from builders.entrance.auth import PASSWORD
from builders.entrance.goals import goal_responder
from builders.entrance.stand import Stand, json_of, serving_stand, set_password, stand_manifest
from pydantic import SecretStr

from hivemind.cell import HoneyClearance
from hivemind.cli.landing import SignedIn
from hivemind.cli.remote import (
    EnrolmentOrder,
    FollowPace,
    GoalAsk,
    ProfileStore,
    enrol_device,
    remote_session,
    submit_and_follow,
)
from hivemind.cli.remote.goals import CHAT_FORBIDDEN_NOTE
from hivemind.entrance.models import AnswerBody, AnsweredView, ChatLine, GoalAccepted
from hivemind.queen import ChatKind
from waggle.clock import SystemClock

_GOAL = GoalAsk("write a haiku about bees", HoneyClearance.C1, None)
_FOLLOW_S = 30.0  # Generous: each goal here finishes in about a second.
# A device without the chat (no honey:clearance:c2) or push (no entrance:push): the device role's
# proposed set less those two.
_NO_VIEWS = (
    "entrance:submit,entrance:answer,observe,cell:virtual,cell:hive_stand,"
    "cell:comb_shield:meadow,cell:comb_shield:propolis,tool:*,net:*,fs:read:**,fs:write:**,"
    "exec:*,llm:*,cell:outside_scratch:**,honey:read:*,honey:write,wax:propose,question:human"
)


class _Recorder:
    """A Follower that keeps everything it is told."""

    def __init__(self) -> None:
        """Start empty."""
        self.accepted: list[GoalAccepted] = []
        self.lines: list[ChatLine] = []
        self.notes: list[str] = []

    def submitted(self, accepted: GoalAccepted) -> None:
        """Keep the acceptance."""
        self.accepted.append(accepted)

    def line(self, line: ChatLine) -> None:
        """Keep a line."""
        self.lines.append(line)

    def note(self, text: str) -> None:
        """Keep a note."""
        self.notes.append(text)


@asynccontextmanager
async def _laptop(stand: Stand, root: Path, *capabilities: str) -> AsyncIterator[SignedIn]:
    """Enrol a laptop, approve it as the console (the proposed set, or ``capabilities``)."""
    invite = json_of(await stand.entrance("invite", "--device", "laptop", "--json"))
    store = ProfileStore(root)
    order = EnrolmentOrder(invite["url"], None, invite["hive_id"], "laptop", "default")
    profile = await enrol_device(store, order, SystemClock())
    granted = ("--capabilities", *capabilities) if capabilities else ()
    approved = await stand.entrance(
        *("approve", profile.device_id, "--spend-cap", "20", "--interactive", "--yes"), *granted
    )
    assert approved.exit_code == 0, approved.output
    async with remote_session(store, "default", SecretStr(PASSWORD), SystemClock()) as board:
        yield board


async def test_a_goal_is_followed_to_its_end_through_the_views(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    follower = _Recorder()

    async with (
        serving_stand(path, goal_responder()) as (stand, _entrance),
        _laptop(stand, tmp_path / "laptop") as board,
    ):
        outcome = await submit_and_follow(board, _GOAL, FollowPace(_FOLLOW_S), follower)
        tasks = await stand.terminal.hive("tasks", "list", "--manifest", str(stand.manifest_path))

    assert not outcome.timed_out
    assert outcome.view.finished_at is not None and not outcome.view.refused
    assert "SUCCEEDED" in tasks.output, tasks.output
    assert [accepted.id for accepted in follower.accepted] == [outcome.view.id]
    assert follower.notes == []


async def test_a_question_reaches_the_follower_and_its_answer_lets_the_goal_finish(
    tmp_path: Path,
) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    follower = _Recorder()

    async with (
        serving_stand(path, goal_responder("Which season?")) as (stand, _entrance),
        _laptop(stand, tmp_path / "laptop") as board,
    ):
        following = asyncio.ensure_future(
            submit_and_follow(board, _GOAL, FollowPace(_FOLLOW_S), follower)
        )
        question = await _question(follower)
        assert question.ref is not None
        answer = AnswerBody(text="spring")
        path_ = f"/v1/inbox/questions/{question.ref}/answer"
        answered = await board.call("POST", path_, answer, AnsweredView)
        outcome = await following
        tasks = await stand.terminal.hive("tasks", "list", "--manifest", str(stand.manifest_path))

    assert question.text == "Which season?"
    assert answered.question_status.value == "ANSWERED"
    assert outcome.view.finished_at is not None
    assert "SUCCEEDED" in tasks.output, tasks.output


async def test_a_device_without_the_views_follows_by_rereading(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)
    follower = _Recorder()
    pace = FollowPace(_FOLLOW_S, recheck_s=0.2)

    async with (
        serving_stand(path, goal_responder()) as (stand, _entrance),
        _laptop(stand, tmp_path / "laptop", _NO_VIEWS) as board,
    ):
        outcome = await submit_and_follow(board, _GOAL, pace, follower)

    assert follower.notes == [CHAT_FORBIDDEN_NOTE]
    assert outcome.view.finished_at is not None


async def test_the_timeout_ends_the_follow_not_the_goal(tmp_path: Path) -> None:
    path = stand_manifest(tmp_path / "stand")
    await set_password(path)

    async with (
        serving_stand(path, goal_responder("Which season?")) as (stand, _entrance),
        _laptop(stand, tmp_path / "laptop") as board,
    ):
        outcome = await submit_and_follow(board, _GOAL, FollowPace(0.5), _Recorder())

    assert outcome.timed_out
    assert outcome.view.finished_at is None and not outcome.view.refused


async def _question(follower: _Recorder) -> ChatLine:
    """Wait until the follower has been told a question, and return it."""
    async with asyncio.timeout(_FOLLOW_S):
        while True:
            for line in follower.lines:
                if line.kind is ChatKind.QUESTION:
                    return line
            await asyncio.sleep(0.05)
