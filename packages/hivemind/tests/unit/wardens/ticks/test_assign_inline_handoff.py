"""Tests for hivemind.wardens.ticks.assign.store_inline_handoff (roadmap step 11.6a, Waggle 1.13).

A Warden that moved onto a device cannot read the Hive Stand's memory store, so the Queen sends the
Handoff a task resumes from inside the `task.assign` itself (`TaskAssign.handoff`). The Warden files
it under `resume_from.event_id` before anything spawns, so the resume path every other resume uses
finds it. These tests drive a real Warden over a scripted worker: the Handoff arrives on the wire,
is stored, and the bee that spawns is handed it. They also pin the things the filing must not do:
replace what the store already holds, invent a taint label, record text on the trail, or touch an
assignment that carries nothing.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/assign.py (codingrules section 3); split by feature (14.2)
    from test_assign_raises.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.assign for the function under test.
    - waggle.messages.task.handoff for HandoffDocument, the wire form of a Handoff.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from builders.memory import make_handoff
from builders.quarantine import make_grant_issued, settle
from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome, yield_then

from hivemind.cell import HoneyClearance
from hivemind.memory import Decision, Handoff, HandoffNotFoundError
from hivemind.pheromone import MemoryEvent, TrailQuery
from hivemind.wardens import WardenDeps
from hivemind.wardens.ticks.assign import store_inline_handoff
from hivemind.wardens.warden import Warden
from waggle.ids import EventId, new_event_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance as WireClearance
from waggle.messages.task import HandoffDecision, HandoffDocument, TaskAssign, WorkerRole


def _document(handoff: Handoff) -> HandoffDocument:
    """The wire form of `handoff`, as the Queen builds it from a stored one."""
    return HandoffDocument(
        goal=handoff.goal,
        progress=handoff.progress,
        decisions=tuple(HandoffDecision(what=d.what, why=d.why) for d in handoff.decisions),
        tried_and_failed=handoff.tried_and_failed,
        constraints=handoff.constraints,
        open_threads=handoff.open_threads,
        next_steps=handoff.next_steps,
        do_not_redo=handoff.do_not_redo,
        pinned_facts=handoff.pinned_facts,
        notes=handoff.notes,
        clearance=WireClearance(handoff.clearance.value),
        written_by=handoff.written_by,
        task_id=handoff.task_id,
    )


def _carrying(deps: WardenDeps, handoff: Handoff, **overrides: object) -> TaskAssign:
    """A task resuming from `handoff`, sent inline under a reference only the Queen's store has."""
    ref = HandoffRef(
        event_id=new_event_id(deps.clock),
        written_at=deps.clock.now(),
        clearance=WireClearance(handoff.clearance.value),
    )
    return make_assignment(
        clock=deps.clock, resume_from=ref, handoff=_document(handoff), **overrides
    )


@dataclass
class _Rig:
    """A started Warden, the Queen's end of its link, and the worker that records its resume."""

    deps: WardenDeps
    queen_end: QueenEnd
    warden: Warden
    worker: ScriptedWorker
    run_task: asyncio.Task[None]

    async def send(self, assignment: TaskAssign) -> None:
        """Send the grant and then the assignment, and wait until a bee has been started."""
        await self.queen_end.send(make_grant_issued(self.deps.clock, assignment))
        await self.queen_end.send(assignment)
        await settle(lambda: bool(self.worker.calls))

    async def stop(self) -> None:
        """Stop the Warden and wait for its loop to end, bounded."""
        await self.warden.stop()
        await asyncio.wait_for(self.run_task, timeout=5.0)


async def _started(prefilled: tuple[EventId, Handoff] | None = None) -> _Rig:
    """Start a Warden over fakes; `prefilled` is a Handoff its store holds before the task comes."""
    worker = ScriptedWorker(yield_then(make_outcome), role=WorkerRole.DRONE)
    deps, queen_end, warden_id = make_warden_deps(worker_factory=lambda role: worker)
    if prefilled is not None:
        await _file(deps, *prefilled)
    warden = Warden(warden_id, deps)
    await warden.start()
    return _Rig(deps, queen_end, warden, worker, asyncio.ensure_future(warden.run()))


async def _file(deps: WardenDeps, event_id: EventId, handoff: Handoff) -> None:
    """Put `handoff` in the Warden's store under `event_id`, the way a checkpoint would."""
    event = MemoryEvent(
        id=event_id,
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor="system",
        kind="memory.checkpoint",
        subject_id=event_id,
        payload={},
    )
    await deps.memory.put_handoff(event_id, handoff, None, event)


async def test_an_inline_handoff_is_filed_under_its_reference_and_the_bee_resumes_from_it() -> None:
    rig = await _started()
    handoff = make_handoff(goal="Carry on from the Hive Stand.", written_by="warden_old")
    assignment = _carrying(rig.deps, handoff)
    assert assignment.resume_from is not None

    await rig.send(assignment)

    resumed = rig.worker.calls[0][2]
    assert resumed is not None and resumed.goal == "Carry on from the Hive Stand."
    stored, clearance = await rig.deps.memory.get_handoff(assignment.resume_from.event_id)
    assert stored == resumed and stored.tainted is None
    assert clearance is HoneyClearance.C1
    await rig.stop()


async def test_a_handoff_keeps_the_clearance_it_carries() -> None:
    rig = await _started()
    royal = make_handoff(clearance=HoneyClearance.C2)
    assignment = _carrying(rig.deps, royal, clearance=WireClearance.C2)
    assert assignment.resume_from is not None

    await rig.send(assignment)

    _, clearance = await rig.deps.memory.get_handoff(assignment.resume_from.event_id)
    assert clearance is HoneyClearance.C2
    await rig.stop()


async def test_the_filing_records_one_memory_event_with_counts_and_no_text() -> None:
    rig = await _started()
    handoff = make_handoff(
        decisions=(Decision(what="A private decision.", why="Because."),),
        pinned_facts=("fact one",),
    )
    assignment = _carrying(rig.deps, handoff)
    assert assignment.resume_from is not None

    await rig.send(assignment)

    events = [e for e in await rig.deps.trail.query(TrailQuery()) if e.kind == "memory.handoff"]
    assert [e.id for e in events] == [assignment.resume_from.event_id]
    assert events[0].payload == {
        "origin": "task.assign",
        "decision_count": 1,
        "pinned_fact_count": 1,
    }
    assert "private decision" not in events[0].model_dump_json()
    await rig.stop()


async def test_a_handoff_the_store_already_holds_is_never_replaced() -> None:
    # The reference is fixed first so the store can be given a different Handoff under it.
    clock_deps, _, _ = make_warden_deps()
    ref_id = new_event_id(clock_deps.clock)
    rig = await _started(prefilled=(ref_id, make_handoff(goal="What this Warden itself wrote.")))
    theirs = make_handoff(goal="What the Queen sends along.")
    ref = HandoffRef(event_id=ref_id, written_at=rig.deps.clock.now(), clearance=WireClearance.C1)
    assignment = make_assignment(clock=rig.deps.clock, resume_from=ref, handoff=_document(theirs))

    await rig.send(assignment)

    resumed = rig.worker.calls[0][2]
    assert resumed is not None and resumed.goal == "What this Warden itself wrote."
    await rig.stop()


async def test_an_assignment_with_no_inline_handoff_files_nothing() -> None:
    rig = await _started()

    await rig.send(make_assignment(clock=rig.deps.clock))

    kinds = [e.kind for e in await rig.deps.trail.query(TrailQuery())]
    assert "memory.handoff" not in kinds
    await rig.stop()


async def test_a_reference_with_no_inline_handoff_invents_none() -> None:
    rig = await _started()
    ref = HandoffRef(
        event_id=new_event_id(rig.deps.clock),
        written_at=rig.deps.clock.now(),
        clearance=WireClearance.C1,
    )

    # Called on its own: a bee resuming from a Handoff nobody holds fails in its own loader, as it
    # always has, and this function must not paper over that by inventing one.
    await store_inline_handoff(rig.warden, make_assignment(clock=rig.deps.clock, resume_from=ref))

    with pytest.raises(HandoffNotFoundError):
        await rig.deps.memory.get_handoff(ref.event_id)
    await rig.stop()
