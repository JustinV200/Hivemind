"""Tests for hivemind.queen.queen.Queen: a Warden cannot report for another Cell's task.

Two Wardens attached, each on a Cell of its own, and one task placed on the first. The second
Warden sends everything a Warden may send about a task: a result that it succeeded, one that it
failed, an Alarm, a question and an answer. The Queen's tick refuses every one before deciding
anything, so the task is neither finished, retried, failed, rebound nor blocked, and each is one
`guard.task_refused` about the second Cell. Its own Warden then finishes it as ever.

Fits into the Hive:
    Mirrors src/hivemind/queen/queen.py (codingrules section 3); split by feature (14.2) from
    test_queen_results.py. Exercises hivemind.queen.inbox.claims together with the Queen's tick.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.inbox.claims for the check under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from builders.cells import make_cell
from builders.queen import make_queen_deps, make_warden_link, plan_responder

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.inbox import TASK_REFUSED_KIND
from hivemind.queen.queen import Queen
from waggle.clock import Clock
from waggle.ids import TaskId, WardenId, new_alarm_id, new_message_id, new_node_id, new_warden_id
from waggle.messages import AlarmSeverity
from waggle.messages.base import WaggleMessage
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.supervision import (
    AlarmContext,
    AlarmKind,
    AlarmRaised,
    Answer,
    AnswerSource,
    Question,
)
from waggle.messages.task import TaskOutcome, TaskResult


def _one_task_plan(goal: str) -> dict[str, object]:
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [
                    {
                        "kind": "FILE_EXISTS",
                        "subject": "scratch/r.txt",
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


def _result(task_id: TaskId, warden_id: WardenId, outcome: TaskOutcome) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        attempt=1,
        outcome=outcome,
        summary="Some outcome.",
        clearance=WireHoneyClearance.C1,
        artifacts=(),
        checked_by=warden_id,
        handoff=None,
        spend=0.0,
        reason="Some reason.",
    )


def _forged(clock: Clock, task_id: TaskId, sender: WardenId) -> tuple[WaggleMessage, ...]:
    """Everything a Warden could say about `task_id`, said by `sender`, which does not hold it."""
    question_id = new_message_id(clock)
    context = AlarmContext(
        task_id=task_id, cell_id=None, worker_id=None, event_id=None, handoff=None
    )
    return (
        _result(task_id, sender, TaskOutcome.SUCCEEDED),
        _result(task_id, sender, TaskOutcome.FAILED),
        AlarmRaised(
            alarm_id=new_alarm_id(clock),
            kind=AlarmKind.PROVIDER_UNAVAILABLE,
            severity=AlarmSeverity.WARNING,
            origin=sender,
            attempts=0,
            raised_at=clock.now(),
            context=context,
            detail="The bound provider did not respond.",
            clearance=WireHoneyClearance.C1,
            reason="Cannot resolve locally.",
        ),
        Question(
            question_id=question_id,
            task_id=task_id,
            asked_by=sender,
            text="Which environment?",
            options=("staging", "prod"),
            clearance=WireHoneyClearance.C1,
            asked_at=clock.now(),
        ),
        Answer(
            question_id=question_id,
            task_id=task_id,
            text="staging",
            chosen_option=0,
            source=AnswerSource.WARDEN,
            clearance=WireHoneyClearance.C1,
        ),
    )


async def _wait_until(condition: Callable[[], Awaitable[bool]], limit: int = 400) -> None:
    """Yield the event loop until `condition()` (an async callable) is True, or give up."""
    for _ in range(limit):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")


async def _refused_kinds(deps: QueenDeps) -> set[object]:
    """The message kind of every `guard.task_refused` on the Queen's trail."""
    events = await deps.trail.query(TrailQuery(kind=TASK_REFUSED_KIND))
    return {event.payload["message"] for event in events}


async def test_no_warden_finishes_fails_blocks_or_answers_for_another_cells_task() -> None:
    provider = FakeLLMProvider(responder=plan_responder(_one_task_plan))
    deps, owner, owner_end = make_queen_deps(fake_provider=provider)
    clock = deps.clock
    other, other_end = make_warden_link(
        deps.identity.hive_id,
        new_warden_id(clock),
        new_node_id(clock),
        make_cell(clock=clock),
        clock,
    )
    queen = Queen(deps)
    await queen.attach_warden(owner)
    await queen.attach_warden(other)
    await queen.submit_goal("One task.", clearance=HoneyClearance.C1)
    assignment = await owner_end.wait_for_assignment()
    run_task = asyncio.ensure_future(queen.run())

    forged = _forged(clock, assignment.task_id, other.warden_id)
    for message in forged:
        # An Answer is a reply, so it carries a correlation id; any id will do for a forgery.
        reply_to = new_message_id(clock) if isinstance(message, Answer) else None
        await other_end.send(message, correlation_id=reply_to)
    kinds = {
        "task.result",
        "supervision.alarm_raised",
        "supervision.question",
        "supervision.answer",
    }
    await _wait_until(lambda: _is(deps, kinds))
    held = await deps.chamber.get(assignment.task_id)
    # Its own Warden still holds it: then its own result, and only that, finishes it.
    await owner_end.send(_result(assignment.task_id, owner.warden_id, TaskOutcome.SUCCEEDED))
    await _wait_until(lambda: _status(deps, assignment.task_id, TaskStatus.SUCCEEDED))
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert (held.status, held.warden_id, held.pending_question_id) == (
        TaskStatus.RUNNING,
        owner.warden_id,
        None,
    )
    events = await deps.trail.query(TrailQuery(kind=TASK_REFUSED_KIND))
    assert {event.subject_id for event in events} == {other.cell.id}
    assert {event.payload["reason"] for event in events} == {"another_cell"}
    assert owner_end.assignments == [assignment]  # No retry, no rebind, nothing more sent.
    await owner_end.close()
    await other_end.close()


async def _is(deps: QueenDeps, kinds: set[str]) -> bool:
    """Whether the Queen has refused a message of every one of `kinds`."""
    return await _refused_kinds(deps) >= kinds


async def _status(deps: QueenDeps, task_id: TaskId, status: TaskStatus) -> bool:
    """Whether `task_id` is in `status` now."""
    return (await deps.chamber.get(task_id)).status is status
