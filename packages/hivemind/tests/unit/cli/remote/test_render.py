"""Test hivemind.cli.remote.render: what a remote Hive says, printed escaped and actionable.

Fits into the Hive:
    Mirrors src/hivemind/cli/remote/render.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hivemind.cell import HoneyClearance
from hivemind.cli.remote import FollowOutcome
from hivemind.cli.remote.render import chat_lines, inbox_lines, outcome_line
from hivemind.entrance.models import AlarmView, ChatLine, GoalView, InboxView, QuestionView
from hivemind.queen import ChatAuthor, ChatKind, GoalRequestState, GoalSource

_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
_TASK = "task_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3"
_ESCAPE = "\x1b[2J"  # A terminal control sequence a hostile string might carry.


def _line(kind: ChatKind, text: str, ref: str | None = None) -> ChatLine:
    """A line the Queen wrote."""
    return ChatLine(
        seq=1,
        id="chat_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        at=_AT,
        author=ChatAuthor.QUEEN,
        kind=kind,
        text=text,
        ref=ref,
        task_id=None,
    )


def _view(state: GoalRequestState, *, refused: bool = False, done: bool = False) -> GoalView:
    """A goal request's view in ``state``."""
    return GoalView(
        id="goalreq_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3",
        state=state,
        goal_id=_TASK if state is GoalRequestState.PLANNED else None,
        budget_usd=None,
        comb_shield=None,
        clearance=HoneyClearance.C1,
        source=GoalSource.TYPED,
        needs_confirmation=False,
        refused=refused,
        received_at=_AT,
        updated_at=_AT,
        confirmed_at=None,
        finished_at=_AT if done else None,
    )


def test_a_question_is_printed_escaped_with_its_options_and_the_answer_command() -> None:
    entry = _line(ChatKind.QUESTION, f"Which season?{_ESCAPE}\n[0] spring\n[1] autumn", "q_1")

    lines = chat_lines(entry)

    assert lines[0].startswith("queen asks [q_1]: Which season?")
    assert all("\x1b" not in line for line in lines)
    assert lines[1:3] == ["  [0] spring", "  [1] autumn"]
    assert lines[-1] == '  answer it: hive inbox --remote answer q_1 "..."'


def test_an_alarm_says_how_to_acknowledge_it_and_a_reply_says_nothing_more() -> None:
    alarm = chat_lines(_line(ChatKind.ALARM, "The Cell stopped answering.", "alarm_1"))
    reply = chat_lines(_line(ChatKind.REPLY, "Done."))

    assert alarm == [
        "alarm [alarm_1]: The Cell stopped answering.",
        "  acknowledge it: hive inbox --remote acknowledge alarm_1",
    ]
    assert reply == ["queen: Done."]


def test_the_humans_own_lines_are_not_echoed_back() -> None:
    mine = _line(ChatKind.MESSAGE, "hello").model_copy(update={"author": ChatAuthor.HUMAN})

    assert chat_lines(mine) == []


def test_the_outcome_names_a_finish_a_refusal_and_a_goal_still_running() -> None:
    finished = outcome_line(FollowOutcome(_view(GoalRequestState.PLANNED, done=True), False))
    refused = outcome_line(FollowOutcome(_view(GoalRequestState.REFUSED, refused=True), False))
    running = outcome_line(FollowOutcome(_view(GoalRequestState.RECEIVED), True))

    assert f"finished at 2026-09-24T12:00:00+00:00 (goal {_TASK})" in finished
    assert "was refused" in refused
    assert "still RECEIVED (goal not planned); the Hive goes on with it." in running


def test_the_inbox_lists_questions_with_options_then_alarms_escaped() -> None:
    inbox = InboxView(
        questions=[
            QuestionView(
                id="question_1",
                task_id=_TASK,
                text=f"Pick one{_ESCAPE}",
                options=["spring", "autumn"],
                clearance=HoneyClearance.C1,
                asked_at=_AT,
            )
        ],
        alarms=[
            AlarmView(
                id="alarm_1",
                kind="CELL_UNREACHABLE",
                severity="WARNING",
                detail="no answer",
                task_id=None,
                raised_at=_AT,
            )
        ],
    )

    lines = inbox_lines(inbox)

    assert lines[0].startswith(f"question question_1 (task {_TASK}): Pick one")
    assert "\x1b" not in lines[0]
    assert lines[1:] == [
        "  [0] spring",
        "  [1] autumn",
        "alarm alarm_1 CELL_UNREACHABLE (WARNING): no answer",
    ]
    assert inbox_lines(InboxView(questions=[], alarms=[])) == ["Nothing waits on the human."]
