"""Tests for the Queen's dispatcher around a held Cell and a task taken back from a lost device.

When a Swarm device is lost the Queen holds its Cell's tasks for a grace period before she judges
it (ADR-0053). The dispatcher's half of that is small and is pinned here through a real Queen: a
held Cell is not chosen for a fresh placement, a retry or a resume addressed to it sends nothing,
and the work picks up again when the hold ends. A task the Queen takes back from a device she gave
up on is placed afresh, and resumes from the Handoff she held for it (`DispatchBook.resumes`):
the reference alone for a Warden that shares her memory store, the document too for a remote one
(a device's Warden that cannot read her store). A resume of a task on a remote Warden whose Cell
wrote the Handoff in its own store, which she never held, sends the reference alone for the Cell to
read: that is every resume on a Virtual Cell (an isolation lift, `hive wake`), and it is proved
here with nothing seeded in her store.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ready/dispatch.py (codingrules section 3); split by
    feature (14.2) from test_queen_dispatch.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.holds for the books the dispatcher reads.
    - tests.unit.queen.dispatcher.test_ready_handoff for the Handoff rule itself.
"""

from __future__ import annotations

import dataclasses

from builders.memory import make_handoff
from builders.queen import WardenEnd, make_queen_deps, plan_responder

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.llm import FakeLLMProvider
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, redispatch, resume_paused
from hivemind.queen.queen import Queen
from waggle.ids import TaskId, new_event_id, new_node_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance as WireHoneyClearance


def _plan(goal: str) -> dict[str, object]:
    """A one-task plan, enough for one task to be placed."""
    acceptance: dict[str, object] = {
        "kind": "FILE_EXISTS",
        "subject": "scratch/done.txt",
        "argv": [],
        "expected": None,
    }
    return {
        "tasks": [
            {
                "key": "root",
                "title": "Root task",
                "objective": f"Do the work for: {goal}",
                "acceptance": [acceptance],
                "needs": {},
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


async def _queen(
    *, hold: bool = False, remote: bool = False
) -> tuple[Queen, WardenLink, WardenEnd]:
    """A Queen with one Cell attached (held at once when asked; its Warden on another node)."""
    provider = FakeLLMProvider(responder=plan_responder(_plan))
    deps, link, end = make_queen_deps(fake_provider=provider)
    if remote:
        link = dataclasses.replace(link, node_id=new_node_id(deps.clock))
    queen = Queen(deps)
    await queen.attach_warden(link)
    if hold:
        deps.dispatch.held.hold(link.cell.id, "link_lost")
    return queen, link, end


async def _submit(queen: Queen) -> TaskId:
    """Submit the one-task goal; its id."""
    return await queen.submit_goal("Write a haiku.", clearance=HoneyClearance.C1)


def _deps(queen: Queen) -> QueenDeps:
    """The collaborators the Queen was built with."""
    return queen._deps


async def _running(queen: Queen, end: WardenEnd, task_id: TaskId) -> Task:
    """The task, once it is placed, assigned and RUNNING."""
    await end.wait_for_assignment()
    task = await _deps(queen).chamber.get(task_id)
    assert task.status is TaskStatus.RUNNING
    return task


async def test_a_held_cell_is_not_chosen_for_a_fresh_placement() -> None:
    queen, _link, _end = await _queen(hold=True)

    task_id = await _submit(queen)

    deps = _deps(queen)
    assert (await deps.chamber.get(task_id)).status is TaskStatus.PENDING  # The only Cell is held.
    decided = await deps.trail.query(TrailQuery(kind="queen.decided"))
    assert "placement_failed" in [event.payload.get("reason") for event in decided]
    assert deps.ledger.live_grants() == ()


async def test_a_task_waiting_on_a_held_cell_is_placed_when_the_hold_ends() -> None:
    queen, link, end = await _queen(hold=True)
    task_id = await _submit(queen)
    deps = _deps(queen)

    deps.dispatch.held.release(link.cell.id)
    await dispatch_ready(deps, queen.wardens)

    assert (await deps.chamber.get(task_id)).status is TaskStatus.RUNNING
    assert (await end.wait_for_assignment()).task_id == task_id


async def test_a_retry_addressed_to_a_held_cell_sends_nothing_until_the_hold_ends() -> None:
    queen, link, end = await _queen()
    task = await _running(queen, end, await _submit(queen))
    deps = _deps(queen)
    booked = len(deps.ledger.live_grants())
    deps.dispatch.held.hold(link.cell.id, "link_lost")

    await redispatch(deps, queen.wardens, task.id, 2)

    assert len(deps.ledger.live_grants()) == booked  # No fresh grant was booked.
    deps.dispatch.held.release(link.cell.id)
    await redispatch(deps, queen.wardens, task.id, 2)
    assert len(deps.ledger.live_grants()) == booked + 1  # And now it goes.


async def test_a_resume_addressed_to_a_held_cell_leaves_the_task_paused() -> None:
    queen, link, end = await _queen()
    task = await _running(queen, end, await _submit(queen))
    deps = _deps(queen)
    await deps.chamber.pause(task.id, "test pause")
    deps.dispatch.held.hold(link.cell.id, "link_lost")

    await resume_paused(deps, queen.wardens, task.id, None, "test resume")

    assert (await deps.chamber.get(task.id)).status is TaskStatus.PAUSED
    deps.dispatch.held.release(link.cell.id)
    await resume_paused(deps, queen.wardens, task.id, None, "test resume")
    assert (await deps.chamber.get(task.id)).status is TaskStatus.RUNNING


async def _take_back(queen: Queen, link: WardenLink, task: Task) -> HandoffRef:
    """Take `task` back from its Warden as the Queen does at the end of a grace; its Handoff."""
    deps = _deps(queen)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    handoff = make_handoff(deps.clock, written_by=link.warden_id, task_id=task.id)
    ref = await write_checkpoint(handoff, task.id, ctx)
    await deps.chamber.pause(task.id, "device lost")
    await deps.chamber.unassign(task.id, "device lost for good")
    deps.dispatch.resumes.note(task.id, ref)
    return ref


async def test_a_task_taken_back_is_placed_afresh_resuming_from_its_handoff() -> None:
    queen, link, end = await _queen()
    task = await _running(queen, end, await _submit(queen))
    first = len(end.assignments)
    ref = await _take_back(queen, link, task)

    await dispatch_ready(_deps(queen), queen.wardens)

    await end.pump_until(lambda: len(end.assignments) > first)
    assignment = end.assignments[-1]
    assert (assignment.task_id, assignment.resume_from) == (task.id, ref)
    assert assignment.handoff is None  # It shares the Queen's store: the reference is enough.
    assert assignment.attempt == 2  # Taken back once: the chamber counted a new attempt.
    assert _deps(queen).dispatch.resumes.take(task.id) is None  # The hint was used up.


async def test_a_task_taken_back_to_a_remote_warden_carries_its_handoff() -> None:
    queen, link, end = await _queen(remote=True)
    task = await _running(queen, end, await _submit(queen))
    first = len(end.assignments)
    ref = await _take_back(queen, link, task)

    await dispatch_ready(_deps(queen), queen.wardens)

    await end.pump_until(lambda: len(end.assignments) > first)
    assignment = end.assignments[-1]
    assert assignment.resume_from == ref
    assert assignment.handoff is not None
    assert assignment.handoff.goal == "Finish wiring the new endpoint."


async def test_a_task_taken_back_without_a_handoff_starts_fresh() -> None:
    queen, _link, end = await _queen()
    task = await _running(queen, end, await _submit(queen))
    first = len(end.assignments)
    deps = _deps(queen)
    await deps.chamber.pause(task.id, "device lost")
    await deps.chamber.unassign(task.id, "device lost for good")  # Nothing noted for it.

    await dispatch_ready(deps, queen.wardens)

    await end.pump_until(lambda: len(end.assignments) > first)
    assert end.assignments[-1].resume_from is None


async def test_a_resume_on_a_remote_warden_sends_the_reference_her_store_never_held() -> None:
    queen, _link, end = await _queen(remote=True)
    task = await _running(queen, end, await _submit(queen))
    first = len(end.assignments)
    deps = _deps(queen)
    # The Cell's bee checkpointed in the Cell's own store; all she has is the reference its
    # `memory.checkpoint` event names (nothing is written to her memory store here).
    cells_own = HandoffRef(
        event_id=new_event_id(deps.clock),
        written_at=deps.clock.now(),
        clearance=WireHoneyClearance.C1,
    )
    await deps.chamber.pause(task.id, "isolated")

    await resume_paused(deps, queen.wardens, task.id, cells_own, "the human lifted the isolation")

    await end.pump_until(lambda: len(end.assignments) > first)
    assignment = end.assignments[-1]
    # The Cell's own taint gate and its Worker's loader both read this reference in the Cell's
    # store; before, the Queen resumed such a task fresh and gave them nothing to read.
    assert assignment.resume_from == cells_own
    assert assignment.handoff is None
    assert (await deps.chamber.get(task.id)).status is TaskStatus.RUNNING
    decided = await deps.trail.query(TrailQuery(kind="queen.decided"))
    assert "resume_fresh" not in [event.payload.get("reason") for event in decided]
