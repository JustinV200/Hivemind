"""Tests for the way out of a quarantine: the Queen resumes a task once its checkpoint is cleared.

Roadmap step 10.6a, closing 10.6c's way out. A task its Warden quarantined is PAUSED, and the
Warden's `warden.intervened` names the checkpoint it wrote. Once a judge's `memory.taint_cleared`
about that checkpoint is newer than the task's last pause, the Queen's tick resumes the task from
exactly that checkpoint (a fresh grant, then the TaskAssign the Warden's gate admits). Not before
the verdict, not from a verdict the gate already refused (the task was paused again after it), and
never onto a Cell that is isolated.

Fits into the Hive:
    Mirrors src/hivemind/queen/quarantine/release.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.quarantine.gate for the Warden's half.
"""

from __future__ import annotations

from builders.isolation import isolation_site, place_running, queen_order
from builders.queen import make_queen_deps

from hivemind.brood_chamber import TaskStatus
from hivemind.pheromone import MemoryEvent, WardenEvent
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.guard_requests import GuardDeps
from hivemind.queen.isolation import isolate_cell
from hivemind.queen.quarantine import resume_cleared
from waggle.clock import FakeClock
from waggle.ids import EventId, TaskId, new_event_id


async def _quarantined(deps: QueenDeps, link: WardenLink) -> tuple[TaskId, EventId]:
    """A task its Warden quarantined: PAUSED, its `warden.intervened` naming the checkpoint."""
    task = await place_running(deps, link)
    checkpoint = new_event_id(deps.clock)
    event = WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=link.warden_id,
        kind="warden.intervened",
        subject_id=link.warden_id,
        payload={"action": "QUARANTINE", "task_id": task.id, "handoff_event_id": checkpoint},
    )
    await deps.trail.record(event)
    await deps.chamber.pause(task.id, "Held by its Warden: quarantined.")
    return task.id, checkpoint


async def _clear(deps: QueenDeps, checkpoint: EventId) -> None:
    """A judge's verdict clearing `checkpoint`, as `clear_taint` records it."""
    event = MemoryEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor="system",
        kind="memory.taint_cleared",
        subject_id=checkpoint,
        payload={"item_kind": "handoff", "source": "quarantine"},
    )
    await deps.trail.record(event)


async def test_a_cleared_checkpoint_lets_its_task_out_from_exactly_that_checkpoint() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock)
    task_id, checkpoint = await _quarantined(deps, link)
    clock.advance(1.0)
    await _clear(deps, checkpoint)

    resumed = await resume_cleared(deps, (link,))

    assert resumed == (task_id,)
    assert (await deps.chamber.get(task_id)).status is TaskStatus.RUNNING
    assignment = await warden_end.wait_for_assignment()
    assert assignment.task_id == task_id
    assert assignment.resume_from is not None and assignment.resume_from.event_id == checkpoint
    assert warden_end.received_kinds.index("grant") < warden_end.received_kinds.index("assignment")
    assert await resume_cleared(deps, (link,)) == ()  # RUNNING now: never resumed twice.
    await warden_end.close()


async def test_nothing_resumes_before_a_judge_clears_the_checkpoint() -> None:
    deps, link, warden_end = make_queen_deps()
    task_id, _checkpoint = await _quarantined(deps, link)

    assert await resume_cleared(deps, (link,)) == ()
    assert (await deps.chamber.get(task_id)).status is TaskStatus.PAUSED
    await warden_end.close()


async def test_a_verdict_older_than_the_last_pause_is_not_tried_again() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock)
    task_id, checkpoint = await _quarantined(deps, link)
    clock.advance(1.0)
    await _clear(deps, checkpoint)
    assert await resume_cleared(deps, (link,)) == (task_id,)
    clock.advance(1.0)
    await deps.chamber.pause(task_id, "Held again: its Warden's gate refused the respawn.")

    assert await resume_cleared(deps, (link,)) == ()
    await warden_end.close()


async def test_nothing_resumes_onto_an_isolated_cell() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=GuardDeps(pause_timeout_s=0.0))
    task_id, checkpoint = await _quarantined(deps, link)
    await isolate_cell(isolation_site(deps, link), queen_order(link.cell.id))
    clock.advance(1.0)
    await _clear(deps, checkpoint)

    assert await resume_cleared(deps, (link,)) == ()
    assert (await deps.chamber.get(task_id)).status is TaskStatus.PAUSED
    await warden_end.close()
