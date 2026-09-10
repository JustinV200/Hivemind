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

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.queen import Queen
from waggle.clock import Clock
from waggle.ids import TaskId, WardenId, new_message_id
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
