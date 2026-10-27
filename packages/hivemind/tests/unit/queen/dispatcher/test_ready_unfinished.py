"""Tests for what a stopping Hive gives back: every task it left on its own Cells, PENDING again.

A Hive that stops (an interrupted or timed-out `hive run`, a `hive serve` shut down) takes its
Cells and Wardens along. A task it leaves ASSIGNED, RUNNING or BLOCKED on one of them is given
back to PENDING (a BLOCKED one's question withdrawn first), so the next start places it afresh;
a task on a Night Veil Cell is ended with it instead, a PAUSED task keeps its hold, a task on a
colonized device's Warden keeps running there (ADR-0053), and a task placed by another Queen on
the same Brood Chamber is never moved.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ready/unfinished.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.ready.unfinished for the code under test.
    - tests/e2e/test_interrupted_run.py for the same rule through a real stop.
"""

from __future__ import annotations

import dataclasses

from builders.human import RecordingHumanChannel
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import (
    Answer,
    AnswerSource,
    BroodChamber,
    ChamberIdentity,
    MemoryTaskStore,
    QuestionStatus,
    Task,
    TaskStatus,
)
from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, requeue_unfinished
from hivemind.queen.dispatcher.ready.unfinished import STOPPED_REASON, VEILED_REASON
from waggle.clock import FakeClock
from waggle.ids import MessageId, new_cell_id, new_warden_id

_MEADOW = CombShieldLevel.MEADOW  # The default tier, for tests about anything but the veil.


async def _running(deps: QueenDeps, link: WardenLink) -> Task:
    """Submit one task and dispatch it onto `link`'s Cell, as this Queen does: RUNNING there."""
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    await dispatch_ready(deps, (link,))
    return await deps.chamber.get(task.id)


async def _blocked(deps: QueenDeps, link: WardenLink) -> tuple[Task, MessageId]:
    """A task of this Queen's, BLOCKED on a question its bee asked; with the question's id."""
    task = await _running(deps, link)
    question = await deps.chamber.ask(task.id, asked_by=link.warden_id, text="Which season?")
    return await deps.chamber.get(task.id), question.id


async def test_a_running_task_is_given_back_unplaced_as_a_new_attempt() -> None:
    deps, link, _end = make_queen_deps(FakeClock())
    task = await _running(deps, link)

    requeued = await requeue_unfinished(deps)

    after = await deps.chamber.get(task.id)
    assert requeued == (task.id,)
    assert after.status is TaskStatus.PENDING
    assert (after.warden_id, after.cell_id, after.bound_tier) == (None, None, None)
    assert after.attempt == task.attempt + 1
    [event] = await deps.trail.query(TrailQuery(kind="task.unassigned"))
    assert event.payload == {"reason": STOPPED_REASON, "from_status": "RUNNING"}


async def test_a_blocked_task_has_its_question_withdrawn_and_its_devices_told() -> None:
    channel = RecordingHumanChannel()
    deps, link, _end = make_queen_deps(FakeClock(), human_channel=channel)
    task, question_id = await _blocked(deps, link)

    requeued = await requeue_unfinished(deps)

    assert requeued == (task.id,)
    assert (await deps.chamber.get(task.id)).status is TaskStatus.PENDING
    assert await deps.chamber.pending_questions() == ()
    [withdrawn] = await deps.trail.query(TrailQuery(kind="task.question_withdrawn"))
    assert withdrawn.payload == {"question_id": question_id, "reason": STOPPED_REASON}
    assert channel.calls == [("question_closed", (question_id, QuestionStatus.WITHDRAWN))]


async def test_a_task_stranded_assigned_by_the_stop_is_given_back_too() -> None:
    deps, link, _end = make_queen_deps(FakeClock())
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    # A stop between the dispatch pass's two writes: placed on her Warden, never started.
    deps.dispatch.placed_on.add(link.warden_id)
    await deps.chamber.assign(task.id, link.warden_id, link.cell.id, "p", bound_tier=_MEADOW)

    assert await requeue_unfinished(deps) == (task.id,)

    assert (await deps.chamber.get(task.id)).status is TaskStatus.PENDING


async def test_a_task_on_a_night_veil_cell_is_ended_with_it() -> None:
    # Codingrules 8.7: a Night Veil Cell is teardown-only, so a stop ends its task, never moves it.
    deps, link, _end = make_queen_deps(FakeClock())
    [task] = await deps.chamber.submit(make_graph_draft({"root": ()}))
    deps.dispatch.placed_on.add(link.warden_id)
    veiled = CombShieldLevel.NIGHT_VEIL
    await deps.chamber.assign(task.id, link.warden_id, link.cell.id, "p", bound_tier=veiled)
    await deps.chamber.start(task.id)
    await deps.chamber.ask(task.id, asked_by=link.warden_id, text="Which season?")

    assert await requeue_unfinished(deps) == ()

    ended = await deps.chamber.get(task.id)
    assert ended.status is TaskStatus.CANCELLED
    assert ended.outcome is not None and ended.outcome.summary == VEILED_REASON
    assert await deps.chamber.pending_questions() == ()


async def test_a_paused_task_keeps_its_hold() -> None:
    deps, link, _end = make_queen_deps(FakeClock())
    task = await _running(deps, link)
    await deps.chamber.pause(task.id, "Clustering: its provider went away.")

    assert await requeue_unfinished(deps) == ()

    paused = await deps.chamber.get(task.id)
    assert paused.status is TaskStatus.PAUSED and paused.warden_id == link.warden_id


async def test_another_queens_task_on_the_same_chamber_is_never_moved() -> None:
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    ours = await _running(deps, link)
    [theirs] = await deps.chamber.submit(make_graph_draft({"other": ()}))
    # Placed by another process's Queen, on a Warden this one never placed anything on.
    elsewhere = (new_warden_id(clock), new_cell_id(clock))
    await deps.chamber.assign(theirs.id, *elsewhere, "p", bound_tier=_MEADOW)
    await deps.chamber.start(theirs.id)

    assert await requeue_unfinished(deps) == (ours.id,)

    assert (await deps.chamber.get(theirs.id)).status is TaskStatus.RUNNING


async def test_a_task_on_a_colonized_devices_warden_keeps_running_through_the_stop() -> None:
    # ADR-0053: a Warden on a colonized device keeps working when the Hive Stand stops, so its
    # task is still its own when it reconnects; giving it back would run the work twice.
    deps, link, _end = make_queen_deps(FakeClock())
    device = dataclasses.replace(link, outlives_the_hive=True)
    task = await _running(deps, device)

    requeued = await requeue_unfinished(deps)

    assert requeued == ()
    assert (await deps.chamber.get(task.id)).status is TaskStatus.RUNNING


async def test_a_queen_that_placed_nothing_gives_nothing_back() -> None:
    deps, _link, _end = make_queen_deps(FakeClock())
    await deps.chamber.submit(make_graph_draft({"root": ()}))

    assert await requeue_unfinished(deps) == ()

    assert await deps.trail.query(TrailQuery(kind="task.unassigned")) == ()


async def test_a_task_answered_from_elsewhere_meanwhile_is_still_given_back() -> None:
    # `hive inbox answer` resumes the task between this read and the withdrawal: read once more.
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    chamber = _AnsweredFirst(MemoryTaskStore(deps.trail), clock, _identity(deps))
    deps = dataclasses.replace(deps, chamber=chamber)
    task, _question_id = await _blocked(deps, link)

    assert await requeue_unfinished(deps) == (task.id,)

    after = await deps.chamber.get(task.id)
    assert after.status is TaskStatus.PENDING and after.warden_id is None


class _AnsweredFirst(BroodChamber):
    """A Brood Chamber whose every question is answered, elsewhere, just before a withdrawal."""

    async def withdraw(self, question_id: MessageId, reason: str) -> Task:
        """Answer the question as another process would, then withdraw it (which now fails)."""
        answer = Answer(
            text="spring",
            source=AnswerSource.HUMAN,
            clearance=HoneyClearance.C2,
            answered_at=self._clock.now(),
        )
        await self.answer(question_id, answer)
        return await super().withdraw(question_id, reason)


def _identity(deps: QueenDeps) -> ChamberIdentity:
    """The Hive, node and actor the replacement chamber stamps its events with."""
    return ChamberIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor="system"
    )
