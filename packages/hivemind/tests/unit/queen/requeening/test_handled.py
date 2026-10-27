"""Tests for hivemind.queen.requeening.handled: a restarted Queen handles nothing twice.

Phase 11's open item: after a restart the Queen's replay window was empty, so an Alarm a Warden
sent again (the unnoticed-loss resend keeps the envelope's id) was handled a second time. Here a
Queen handles an Alarm, a second Queen over the same stores comes up, and the very same envelope
arriving again is dropped while a fresh Alarm still reaches the human. And a question asked
before the restart still has its answer forwarded to the Warden that asked, under the wire id
that Warden matches it by: the wiring the first Queen held only in memory is read back, even
when the restart comes long after the question, and is forgotten once the question closes.

Fits into the Hive:
    Mirrors src/hivemind/queen/requeening/handled.py (codingrules section 3), through the
    Queen's own tick, the one caller of `is_replay` and `note_handled`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.requeening.handled for the module under test.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable

import pytest
from builders.queen import WardenEnd, make_queen_deps, make_warden_link, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.queen import Queen
from hivemind.queen.requeening import RequeeningBook, restore_handled, take_snapshot
from waggle.clock import Clock, FakeClock
from waggle.ids import AlarmId, HiveId, TaskId, new_alarm_id, new_message_id, new_worker_id
from waggle.messages import AlarmSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised, Question
from waggle.transport import REPLAY_WINDOW_S

_WAIT_S = 5.0  # Every await here is local and in memory; this only bounds a hang.


def _plan(goal: str) -> dict[str, object]:
    """One task, so the goal itself is the task the Warden reports on."""
    acceptance: dict[str, object] = {
        "kind": "FILE_EXISTS",
        "subject": "scratch/done.txt",
        "argv": [],
        "expected": None,
    }
    task: dict[str, object] = {
        "key": "root",
        "title": "Root task",
        "objective": f"Do the work for: {goal}",
        "acceptance": [acceptance],
        "needs": {},
        "clearance": "C1",
        "depends_on": [],
    }
    return {"tasks": [task]}


def _alarm(clock: Clock, task_id: TaskId) -> AlarmRaised:
    """A GRANT_EXCEEDED Alarm: the shipped policy escalates it to the human at once."""
    return AlarmRaised(
        alarm_id=new_alarm_id(clock),
        kind=AlarmKind.GRANT_EXCEEDED,
        severity=AlarmSeverity.WARNING,
        origin=new_worker_id(clock),
        attempts=0,
        raised_at=clock.now(),
        context=AlarmContext(
            task_id=task_id, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail="Over its grant.",
        clearance=WireHoneyClearance.C1,
        reason="Only the Queen divides Forage.",
    )


async def _started() -> tuple[Queen, WardenLink, WardenEnd, TaskId]:
    """A Queen with one Warden attached and one goal assigned to it."""
    deps, link, warden_end = make_queen_deps(
        fake_provider=FakeLLMProvider(responder=plan_responder(_plan))
    )
    queen = Queen(deps)
    await queen.attach_warden(link)
    goal_id = await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)
    await warden_end.wait_for_assignment()
    return queen, link, warden_end, goal_id


async def _restarted(deps: QueenDeps, link: WardenLink) -> tuple[Queen, WardenEnd]:
    """A second Queen over the same stores, her memory empty, and the same Warden redialed."""
    book = RequeeningBook(store=deps.requeening.store)
    queen = Queen(dataclasses.replace(deps, requeening=book))
    await restore_handled(queen)
    again, warden_end = make_warden_link(
        HiveId(link.hop.sender), link.warden_id, link.hop.node_id, link.cell, deps.clock
    )
    await queen.attach_warden(again)
    return queen, warden_end


async def _until(condition: Callable[[], bool]) -> None:
    """Yield until `condition()` holds; give up after a bounded number of turns."""
    for _ in range(500):
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("The condition never held.")


async def _running(queen: Queen, body: Callable[[], object]) -> None:
    """Run `queen` while `body` (a coroutine function) runs, then stop her."""
    loop = asyncio.ensure_future(queen.run())
    try:
        await asyncio.wait_for(body(), _WAIT_S)  # type: ignore[arg-type]
    finally:
        await queen.stop()
        await asyncio.wait_for(loop, _WAIT_S)


async def test_an_alarm_she_handled_before_her_restart_is_dropped_when_sent_again() -> None:
    first, link, warden_end, goal_id = await _started()
    resent = warden_end.envelope(_alarm(first._deps.clock, goal_id))

    async def escalate_once() -> None:
        await warden_end.send_envelope(resent)
        await _until(lambda: bool(first.human_inbox.alarms))

    await _running(first, escalate_once)
    second, second_end = await _restarted(first._deps, link)
    fresh = _alarm(first._deps.clock, goal_id)

    async def resend_then_raise() -> None:
        await second_end.send_envelope(resent)  # The unnoticed-loss resend: the same id.
        await second_end.send(fresh)
        await _until(lambda: bool(second.human_inbox.alarms))

    await _running(second, resend_then_raise)

    assert [alarm.id for alarm in second.human_inbox.alarms] == [AlarmId(fresh.alarm_id)]


@pytest.mark.parametrize("late", [False, True], ids=["inside_window", "after_window"])
async def test_a_question_asked_before_her_restart_has_its_answer_forwarded_after_it(
    late: bool,
) -> None:
    # A human may answer long after the question: a restart past the replay window, after a
    # snapshot pruned by age, still restores the wiring of a question that is still open.
    first, link, warden_end, goal_id = await _started()
    clock = first._deps.clock
    assert isinstance(clock, FakeClock)
    question = Question(
        question_id=new_message_id(clock),
        task_id=goal_id,
        asked_by=link.warden_id,
        text="Which season?",
        options=("spring", "autumn"),
        clearance=WireHoneyClearance.C1,
        asked_at=clock.now(),
    )

    async def ask() -> None:
        await warden_end.send(question)
        for _ in range(500):  # Yield until her tick has blocked the task on it.
            if (await first._deps.chamber.get(goal_id)).status is TaskStatus.BLOCKED:
                return
            await asyncio.sleep(0)

    await _running(first, ask)
    if late:
        clock.advance(REPLAY_WINDOW_S + 60.0)
        await take_snapshot(first._deps, first.wardens)
    second, second_end = await _restarted(first._deps, link)
    [asked] = await first._deps.chamber.pending_questions()

    await second.answer_question(asked.id, "spring")
    answer = await asyncio.wait_for(second_end.wait_for_answer(), _WAIT_S)
    await take_snapshot(second._deps, second.wardens)  # The question is closed now.

    assert answer.question_id == question.question_id  # The id the asking Warden matches.
    assert await second._deps.requeening.store.wired_questions() == ()
