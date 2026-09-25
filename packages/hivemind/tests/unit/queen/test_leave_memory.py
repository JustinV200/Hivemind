"""Tests for hivemind.queen.leave_memory: "keep for this whole goal" answers itself, once.

Fits into the Hive:
    Mirrors src/hivemind/queen/leave_memory.py (codingrules section 3); follows
    test_queen_questions.py's own Queen+WardenEnd rehearsal pattern for roadmap step 5.0d's own
    "one goal asks once, not once per file" requirement.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.leave_memory for the module under test.
    - .claude/roadmap.md step 5.0d for "remembered by the Queen per goal and Cell."
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import Answer, AnswerSource, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.memory import MemoryContext, Note, add_note
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen import answer_note_author, sync_answers_from_chamber
from hivemind.queen.deps import QueenDeps
from hivemind.queen.leave_memory import LeaveMemoryEntry, is_leave_question
from hivemind.queen.queen import Queen
from hivemind.supervision.capping.checks.human import LEAVE_QUESTION_OPTIONS
from waggle.clock import Clock, FakeClock
from waggle.ids import (
    MessageId,
    TaskId,
    WardenId,
    new_event_id,
    new_message_id,
    new_task_id,
    new_warden_id,
)
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import Answer as WireAnswer
from waggle.messages.supervision import AnswerSource as WireAnswerSource
from waggle.messages.supervision import Question


def _single_task_plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/done.txt",
                        "argv": [],
                        "expected": None,
                    }
                ],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def _leave_question(clock: Clock, *, task_id: TaskId, warden_id: WardenId, text: str) -> Question:
    """Build a leave-shaped Question: its own closed options are what `is_leave_question` reads."""
    return Question(
        question_id=new_message_id(clock),
        task_id=task_id,
        asked_by=warden_id,
        text=text,
        options=LEAVE_QUESTION_OPTIONS,
        clearance=WireHoneyClearance.C1,
        asked_at=clock.now(),
    )


async def _wait_until(condition: Callable[[], Awaitable[bool]], limit: int = 200) -> None:
    """Yield the event loop until `condition()` (an async callable) is True, or give up."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")


async def _blocked(deps: QueenDeps, task_id: TaskId) -> bool:
    """True once `task_id` has moved to BLOCKED; a `_wait_until` condition."""
    task = await deps.chamber.get(task_id)
    return task.status is TaskStatus.BLOCKED


async def _wait_for_new_answer(
    warden_end: WardenEnd, answers_before: int, *, limit: int = 400
) -> WireAnswer:
    """Pump `warden_end` until it holds more answers than `answers_before`, and return the newest.

    `WardenEnd.wait_for_answer`'s own "ready" check is `bool(self.answers)`: once one Answer has
    already arrived, a second call would return instantly without pumping a new envelope at all.
    """
    await warden_end.pump_until(lambda: len(warden_end.answers) > answers_before, limit=limit)
    return warden_end.answers[-1]


async def _ask_and_answer_first_leaving(
    queen: Queen, deps: QueenDeps, warden_end: WardenEnd, goal_id: TaskId, warden_id: WardenId
) -> tuple[Question, WireAnswer]:
    """Send one leave Question, wait for BLOCKED, then answer "keep for this whole goal".

    The setup both roadmap-5.0d tests below share, before either looks at what a *second* leave
    Question does with the memory this leaves behind.
    """
    first = _leave_question(
        deps.clock, task_id=goal_id, warden_id=warden_id, text="Keep checker.exe?"
    )
    await warden_end.send(first)
    await _wait_until(lambda: _blocked(deps, goal_id))
    pending = await queen.human_inbox.pending_questions(deps.chamber)
    chamber_question_id = pending[0].id
    # "keep for this whole goal" is chosen_option 1 (LEAVE_QUESTION_OPTIONS[1]); only a HUMAN
    # source is ever remembered (module docstring's own "Key invariants").
    await _answer_like_hive_inbox_answer(
        deps, chamber_question_id, text="keep for this whole goal", chosen_option=1
    )
    await sync_answers_from_chamber(queen)
    first_answer = await warden_end.wait_for_answer()
    return first, first_answer


async def _answer_like_hive_inbox_answer(
    deps: QueenDeps, chamber_question_id: MessageId, *, text: str, chosen_option: int
) -> None:
    """Simulate `hive inbox answer ... --option` (`cli/readback/inbox.py`'s own two writes).

    The cross-process path that remembers "keep for this whole goal" (`hivemind.queen.questions.
    _forward_from_note`, called from `sync_answers_from_chamber` below); the in-process
    `Queen.answer_question` path the Hive Entrance uses remembers it too (roadmap step 10.5,
    tests/unit/queen/test_queen_chat.py).
    """
    answer = Answer(
        text=text,
        chosen_option=chosen_option,
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
        answered_at=deps.clock.now(),
    )
    await deps.chamber.answer(chamber_question_id, answer)
    note = Note(
        id=new_event_id(deps.clock),
        author=answer_note_author(chamber_question_id),
        text=text,
        clearance=HoneyClearance.C2,
        written_at=deps.clock.now(),
        chosen_option=chosen_option,
    )
    memory_ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    await add_note(note, memory_ctx)


# ──────────────────────────────────────────────────────────────────────────────
# is_leave_question
# ──────────────────────────────────────────────────────────────────────────────


def test_is_leave_question_true_for_the_three_closed_options() -> None:
    clock = FakeClock()
    question = _leave_question(
        clock, task_id=new_task_id(clock), warden_id=new_warden_id(clock), text="Keep it?"
    )

    assert is_leave_question(question) is True


def test_is_leave_question_false_for_an_ordinary_question() -> None:
    clock = FakeClock()
    question = Question(
        question_id=new_message_id(clock),
        task_id=new_task_id(clock),
        asked_by=new_warden_id(clock),
        text="Which season?",
        options=("spring", "summer"),
        clearance=WireHoneyClearance.C1,
        asked_at=clock.now(),
    )

    assert is_leave_question(question) is False


# ──────────────────────────────────────────────────────────────────────────────
# End-to-end through a real Queen+WardenEnd: "one goal asks once"
# ──────────────────────────────────────────────────────────────────────────────


async def test_keep_for_this_whole_goal_answers_a_later_leaving_without_asking_again() -> None:
    """Roadmap step 5.0d: the second leave Question for the same goal+Cell never blocks."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Install a checker.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    first, first_answer = await _ask_and_answer_first_leaving(
        queen, deps, warden_end, goal_id, link.warden_id
    )
    resumed = await deps.chamber.get(goal_id)
    assert resumed.status is TaskStatus.RUNNING

    # The memory is keyed by (goal_id, cell_id); nothing else should have to ask the human again.
    entry = queen._leave_memory[(goal_id, link.cell.id)]
    assert isinstance(entry, LeaveMemoryEntry)
    assert entry.text == "keep for this whole goal"

    # A second, different leave Question for the same task (same goal, same Cell): answered
    # instantly from memory, never surfacing in the human inbox at all.
    second = _leave_question(
        deps.clock, task_id=goal_id, warden_id=link.warden_id, text="Keep license.txt?"
    )
    answers_before = len(warden_end.answers)
    await warden_end.send(second)
    second_answer = await _wait_for_new_answer(warden_end, answers_before)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    # The task never went BLOCKED a second time: the memory answered it before chamber.ask ran.
    still_pending = await queen.human_inbox.pending_questions(deps.chamber)
    assert still_pending == ()
    assert first_answer.question_id == first.question_id
    assert second_answer.question_id == second.question_id
    # Still tagged HUMAN, and still "keep" (option 0): roadmap step 5.0d's own "that remembered
    # answer must still count as the human's."
    assert second_answer.source is WireAnswerSource.HUMAN
    assert second_answer.chosen_option == 0
    assert second_answer.text == "keep for this whole goal"
    await warden_end.close()


async def test_leave_remembered_is_recorded_on_the_trail_with_the_source_question() -> None:
    """Roadmap step 5.0d: auditable which human answer a remembered reuse derives from."""
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Install a checker.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    await _ask_and_answer_first_leaving(queen, deps, warden_end, goal_id, link.warden_id)

    second = _leave_question(
        deps.clock, task_id=goal_id, warden_id=link.warden_id, text="Keep license.txt?"
    )
    # As in the previous test: wait for a *new* Answer, not just "any Answer at all".
    answers_before = len(warden_end.answers)
    await warden_end.send(second)
    await _wait_for_new_answer(warden_end, answers_before)

    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    events = await deps.trail.query(TrailQuery())
    remembered = [event for event in events if event.kind == "queen.leave_remembered"]
    assert len(remembered) == 1
    assert remembered[0].payload["goal_id"] == goal_id
    assert remembered[0].payload["cell_id"] == link.cell.id
    await warden_end.close()
