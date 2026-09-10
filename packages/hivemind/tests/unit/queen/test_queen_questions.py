"""Tests for hivemind.queen.queen.Queen: a Warden-forwarded Question, and Queen.answer_question.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_dispatch.py, test_queen_results.py, test_queen_alarms.py,
    test_queen_liveness.py, test_queen_supervisor.py and test_queen_invariants.py. Exercises
    hivemind.queen.questions together with the Queen's own tick, and doubles as roadmap step
    3.22 scenario (d)'s own unit-level rehearsal: a Question blocks its task and surfaces in the
    human inbox, and answering it (by the Brood Chamber's own id, what a human sees) resumes the
    task and forwards the answer tagged with the *original* wire `question_id` -- never the
    chamber's own internal id, which is a different value on purpose (`hivemind.brood_chamber.
    questions`'s own module docstring) -- since that original id alone is what
    `hivemind.wardens.ticks.questions.forward_answer` matches back to the blocked sub-bee.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.questions for the module under test.
    - .claude/roadmap.md step 3.22 scenario (d) for the block-then-answer e2e this rehearses.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.queen import make_queen_deps, plan_responder

from hivemind.brood_chamber import Answer, AnswerSource, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.memory import MemoryContext, Note, add_note
from hivemind.queen import answer_note_author, sync_answers_from_chamber
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from waggle.clock import Clock
from waggle.ids import MessageId, TaskId, WardenId, new_event_id, new_message_id
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
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


def _question(clock: Clock, *, task_id: TaskId, warden_id: WardenId) -> Question:
    return Question(
        question_id=new_message_id(clock),
        task_id=task_id,
        asked_by=warden_id,
        text="Which environment should the haiku target?",
        options=("staging", "prod"),
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


async def test_question_blocks_the_task_and_surfaces_in_the_human_inbox() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    question = _question(deps.clock, task_id=goal_id, warden_id=link.warden_id)
    await warden_end.send(question)

    async def _blocked() -> bool:
        task = await deps.chamber.get(goal_id)
        return task.status is TaskStatus.BLOCKED

    await _wait_until(_blocked)

    queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    blocked = await deps.chamber.get(goal_id)
    assert blocked.status is TaskStatus.BLOCKED
    pending = await queen.human_inbox.pending_questions(deps.chamber)
    assert len(pending) == 1
    # The Brood Chamber mints its own id for a stored Question (module docstring): never the same
    # value as the wire Question.question_id the Warden forwarded.
    assert pending[0].id != question.question_id
    assert blocked.pending_question_id == pending[0].id
    assert pending[0].asked_by == link.warden_id  # Survives forwarding (module docstring).
    await warden_end.close()


async def test_answer_question_resumes_the_task_and_forwards_the_answer_to_its_warden() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    question = _question(deps.clock, task_id=goal_id, warden_id=link.warden_id)
    await warden_end.send(question)

    async def _blocked() -> bool:
        task = await deps.chamber.get(goal_id)
        return task.status is TaskStatus.BLOCKED

    await _wait_until(_blocked)
    pending = await queen.human_inbox.pending_questions(deps.chamber)
    chamber_question_id = pending[0].id  # What a human actually sees and answers by.

    resumed = await queen.answer_question(chamber_question_id, "Use staging.")
    answer = await warden_end.wait_for_answer()

    queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert resumed.status is TaskStatus.RUNNING
    assert resumed.pending_question_id is None
    # The forwarded Answer carries the *original* wire question_id, not the chamber's own id:
    # hivemind.wardens.ticks.questions.forward_answer matches on that original id alone.
    assert answer.question_id == question.question_id
    assert answer.task_id == goal_id
    assert answer.text == "Use staging."
    still_pending = await queen.human_inbox.pending_questions(deps.chamber)
    assert still_pending == ()  # Answered questions never linger.
    await warden_end.close()


async def _answer_only(deps: QueenDeps, chamber_question_id: MessageId, text: str) -> None:
    """The first of `hive inbox answer`'s own two writes (cli/inbox.py): `chamber.answer` alone.

    Resumes the task with no forwarding of its own; see `_leave_note_only` for the second write,
    the one `sync_answers_from_chamber` actually reads back (queen/questions.py's own module
    docstring: the Brood Chamber's public API has no way to read an answer's text back out).
    """
    answer = Answer(
        text=text,
        chosen_option=None,
        source=AnswerSource.HUMAN,
        clearance=HoneyClearance.C2,
        answered_at=deps.clock.now(),
    )
    await deps.chamber.answer(chamber_question_id, answer)


async def _leave_note_only(deps: QueenDeps, chamber_question_id: MessageId, text: str) -> None:
    """The second write: the Note `sync_answers_from_chamber` reads back (see `_answer_only`)."""
    note = Note(
        id=new_event_id(deps.clock),
        author=answer_note_author(chamber_question_id),
        text=text,
        clearance=HoneyClearance.C2,
        written_at=deps.clock.now(),
    )
    memory_ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    await add_note(note, memory_ctx)


async def _answer_directly_and_leave_a_note(
    deps: QueenDeps, chamber_question_id: MessageId, text: str
) -> None:
    """Simulate `hive inbox answer`'s own two writes (cli/inbox.py) together, bypassing `Queen`."""
    await _answer_only(deps, chamber_question_id, text)
    await _leave_note_only(deps, chamber_question_id, text)


async def test_sync_answers_from_chamber_forwards_a_note_a_separate_process_left() -> None:
    """Rehearses `hive inbox answer`'s own cross-process handoff at the unit level.

    See queen/questions.py's own module docstring: a separate process cannot call `Queen.
    answer_question` (no live link into a running Queen this phase), so it calls `chamber.answer`
    directly and leaves a Note keyed by the Brood Chamber's own Question id; `sync_answers_from_
    chamber` is what a running `hive run`'s own poll loop calls to pick that up and forward it to
    the blocked sub-bee's own Warden, tagged with the *original* wire question_id.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    question = _question(deps.clock, task_id=goal_id, warden_id=link.warden_id)
    await warden_end.send(question)

    async def _blocked() -> bool:
        task = await deps.chamber.get(goal_id)
        return task.status is TaskStatus.BLOCKED

    await _wait_until(_blocked)
    pending = await queen.human_inbox.pending_questions(deps.chamber)
    chamber_question_id = pending[0].id  # What a human (and hive inbox answer) answers by.
    await _answer_directly_and_leave_a_note(deps, chamber_question_id, "Use staging.")

    forwarded = await sync_answers_from_chamber(queen)
    forwarded_answer = await warden_end.wait_for_answer()

    queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert forwarded == 1
    # The forwarded Answer carries the *original* wire question_id (module docstring), matching
    # test_answer_question_resumes_the_task_and_forwards_the_answer_to_its_warden above.
    assert forwarded_answer.question_id == question.question_id
    assert forwarded_answer.task_id == goal_id
    assert forwarded_answer.text == "Use staging."
    resumed = await deps.chamber.get(goal_id)
    assert resumed.status is TaskStatus.RUNNING
    await warden_end.close()


async def test_sync_answers_from_chamber_is_a_no_op_while_the_task_is_still_blocked() -> None:
    """A poll landing before `hive inbox answer` ever runs finds nothing to forward.

    And leaves the question's own bookkeeping in place for the next poll to find.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    question = _question(deps.clock, task_id=goal_id, warden_id=link.warden_id)
    await warden_end.send(question)

    async def _blocked() -> bool:
        task = await deps.chamber.get(goal_id)
        return task.status is TaskStatus.BLOCKED

    await _wait_until(_blocked)

    forwarded = await sync_answers_from_chamber(queen)

    queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert forwarded == 0
    still_blocked = await deps.chamber.get(goal_id)
    assert still_blocked.status is TaskStatus.BLOCKED
    await warden_end.close()


async def test_sync_answers_from_chamber_retries_when_the_note_has_not_landed_yet() -> None:
    """Fix 4b: a poll landing between `chamber.answer()` and its own Note write must retry.

    Interleaves the two `hive inbox answer` writes `_answer_directly_and_leave_a_note` normally
    makes together: `chamber.answer` alone first (moving the task off BLOCKED with no Note yet
    for `sync_answers_from_chamber` to find, exactly `cli/readback/inbox.py::_answer`'s own two
    separate store writes), a sync that must leave its own tracking in place rather than drop it
    and lose the answer for good, then the Note, then a second sync that finally forwards it.
    """
    provider = FakeLLMProvider(responder=plan_responder(_single_task_plan))
    deps, link, warden_end = make_queen_deps(fake_provider=provider)
    queen = Queen(deps)
    queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    question = _question(deps.clock, task_id=goal_id, warden_id=link.warden_id)
    await warden_end.send(question)

    async def _blocked() -> bool:
        task = await deps.chamber.get(goal_id)
        return task.status is TaskStatus.BLOCKED

    await _wait_until(_blocked)
    pending = await queen.human_inbox.pending_questions(deps.chamber)
    chamber_question_id = pending[0].id

    # Only the chamber write lands first; the Note (the second, separate write) has not yet.
    await _answer_only(deps, chamber_question_id, "Use staging.")
    first_sync = await sync_answers_from_chamber(queen)
    resumed = await deps.chamber.get(goal_id)

    # Now the second write lands.
    await _leave_note_only(deps, chamber_question_id, "Use staging.")
    second_sync = await sync_answers_from_chamber(queen)
    forwarded_answer = await warden_end.wait_for_answer()

    queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert first_sync == 0  # No Note yet: nothing to forward, but nothing dropped either.
    assert resumed.status is TaskStatus.RUNNING  # The chamber write alone already moved this.
    assert second_sync == 1  # The retry found the Note this time and forwarded it.
    assert forwarded_answer.question_id == question.question_id  # The original wire id, not ours.
    assert forwarded_answer.text == "Use staging."
    await warden_end.close()
