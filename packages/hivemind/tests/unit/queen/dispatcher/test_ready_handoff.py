"""Unit tests for hivemind.queen.dispatcher.ready.handoff: the Handoff a resuming task carries.

A task resumes from a Handoff named by a reference; a Warden that does not share the Queen's
memory store cannot read it there, so the Queen carries the document inline when she holds it
(ADR-0052, Waggle 1.13). The cases walk the rule with a real memory store and real checkpoints: a
Warden on the Queen's own node is sent the reference alone; a remote one (a migrated device's, a
Virtual Cell's) is sent the document as well, equal to what she stored, when she holds it, and the
reference alone when she does not (its Cell wrote the Handoff in its own store, where its resume
reads it: the scene with `held=False` seeds nothing in hers, as a Virtual Cell's Handoff never is);
a tainted Handoff and one above the task's clearance never travel (the task resumes fresh and says
why on the trail); a Handoff whose assignment would not fit a frame is dropped with its reference.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ready/handoff.py (codingrules section 3); the assignment
    that carries it is `test_ready.py`'s.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.ready.handoff for the module under test.
    - tests.unit.queen.test_queen_dispatch_hold for the same through a whole Queen.
"""

from __future__ import annotations

import dataclasses

from builders.isolation import place_running
from builders.memory import make_handoff
from builders.queen import make_queen_deps
from builders.taint import make_stamp

from hivemind.brood_chamber import Task
from hivemind.cell import HoneyClearance
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.memory.taint import TaintedKind, TaintScope, taint_memory
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.ready.assign import AssignmentTerms, _task_assign
from hivemind.queen.dispatcher.ready.handoff import (
    REASON_ABOVE_CLEARANCE,
    REASON_TAINTED,
    REASON_TOO_LARGE,
    ResumePlan,
    fit_in_frame,
    resume_plan,
)
from waggle.ids import new_event_id, new_grant_id, new_node_id
from waggle.messages import HandoffRef
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import HandoffDocument, TaskAssign


async def _scene(
    *, remote: bool, held: bool = True
) -> tuple[QueenDeps, WardenLink, Task, HandoffRef]:
    """A Queen, the link a task is on (remote: signing as another node) and a Handoff's reference.

    With `held` the Handoff is in her memory store; without it nothing is, and the reference is
    one a Cell's own bee made in the Cell's own store, as a Virtual Cell's and a colonized
    device's are: the `memory.checkpoint` event its trail shipped to her names it, and that is
    all she has.
    """
    deps, link, _end = make_queen_deps()
    task = await place_running(deps, link)
    if remote:
        link = dataclasses.replace(link, node_id=new_node_id(deps.clock))
    if not held:
        cells_own = HandoffRef(
            event_id=new_event_id(deps.clock),
            written_at=deps.clock.now(),
            clearance=WireHoneyClearance.C1,
        )
        return deps, link, task, cells_own
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    handoff = make_handoff(deps.clock, written_by=link.warden_id, task_id=task.id)
    return deps, link, task, await write_checkpoint(handoff, task.id, ctx)


async def _fresh_reasons(deps: QueenDeps) -> list[object]:
    """The `detail` of each `resume_fresh` decision the Queen recorded, oldest first."""
    decided = await deps.trail.query(TrailQuery(kind="queen.decided"))
    return [e.payload["detail"] for e in decided if e.payload.get("reason") == "resume_fresh"]


async def test_no_reference_is_a_fresh_start_with_nothing_to_carry() -> None:
    deps, link, task, _ref = await _scene(remote=True)

    assert await resume_plan(deps, link, task, None) == ResumePlan()
    assert await _fresh_reasons(deps) == []


async def test_a_warden_on_the_queens_own_node_is_sent_the_reference_alone() -> None:
    deps, link, task, ref = await _scene(remote=False)

    plan = await resume_plan(deps, link, task, ref)

    assert plan == ResumePlan(resume_from=ref, handoff=None)


async def test_a_warden_whose_link_proved_the_queens_own_node_is_local_too() -> None:
    deps, link, task, ref = await _scene(remote=False)
    link = dataclasses.replace(link, node_id=deps.identity.node_id)

    plan = await resume_plan(deps, link, task, ref)

    assert plan.handoff is None and plan.resume_from == ref


async def test_a_remote_warden_is_sent_the_document_with_the_reference() -> None:
    deps, link, task, ref = await _scene(remote=True)

    plan = await resume_plan(deps, link, task, ref)

    assert plan.resume_from == ref
    stored = make_handoff(deps.clock, written_by=link.warden_id, task_id=task.id)
    assert plan.handoff == HandoffDocument.model_validate(
        stored.model_dump(mode="json", exclude={"tainted"})
    )
    assert await _fresh_reasons(deps) == []


async def test_a_tainted_handoff_never_travels_and_the_task_resumes_fresh() -> None:
    deps, link, task, ref = await _scene(remote=True)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    scope = TaintScope(
        authors=frozenset({link.warden_id}),
        since=deps.clock.now(),
        kinds=frozenset({TaintedKind.HANDOFF}),
    )
    await taint_memory(scope, make_stamp(), ctx)

    plan = await resume_plan(deps, link, task, ref)

    assert plan == ResumePlan()
    assert await _fresh_reasons(deps) == [REASON_TAINTED]


async def test_a_handoff_above_the_tasks_clearance_never_travels() -> None:
    deps, link, task, _ref = await _scene(remote=True)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    royal = make_handoff(deps.clock, clearance=HoneyClearance.C2, task_id=task.id)
    ref = await write_checkpoint(royal, task.id, ctx)
    assert task.spec.clearance.rank < HoneyClearance.C2.rank  # The task's bee may not read it.

    plan = await resume_plan(deps, link, task, ref)

    assert plan == ResumePlan()
    assert await _fresh_reasons(deps) == [REASON_ABOVE_CLEARANCE]


async def test_a_handoff_her_store_lacks_is_sent_by_reference_alone_to_a_remote_warden() -> None:
    deps, link, task, cells_own = await _scene(remote=True, held=False)

    plan = await resume_plan(deps, link, task, cells_own)

    # Its Warden reads it from its own store, where its bee wrote it; she is not the one to say no.
    assert plan == ResumePlan(resume_from=cells_own, handoff=None)
    assert await _fresh_reasons(deps) == []


async def test_a_local_warden_is_sent_a_reference_it_may_find_in_the_store_it_shares() -> None:
    deps, link, task, _ref = await _scene(remote=False)
    elsewhere = HandoffRef(
        event_id=new_event_id(deps.clock),
        written_at=deps.clock.now(),
        clearance=WireHoneyClearance.C1,
    )

    plan = await resume_plan(deps, link, task, elsewhere)

    assert plan == ResumePlan(resume_from=elsewhere)  # It shares her store, and will find it.


async def _assign_with(
    deps: QueenDeps, link: WardenLink, task: Task, ref: HandoffRef
) -> tuple[ResumePlan, TaskAssign]:
    """The plan for `ref` and the TaskAssign built from it, as the dispatcher builds it."""
    plan = await resume_plan(deps, link, task, ref)
    terms = AssignmentTerms(attempt=2, resume_from=plan.resume_from, handoff=plan.handoff)
    return plan, _task_assign(task, link.cell.id, new_grant_id(deps.clock), terms)


async def test_an_assignment_that_fits_is_sent_with_its_handoff() -> None:
    deps, link, task, ref = await _scene(remote=True)
    _plan, assign = await _assign_with(deps, link, task, ref)

    sent = await fit_in_frame(deps, link, task, assign)

    assert sent is assign
    assert sent.handoff is not None


async def test_an_assignment_without_a_handoff_is_never_touched() -> None:
    deps, link, task, _ref = await _scene(remote=True)
    assign = _task_assign(task, link.cell.id, new_grant_id(deps.clock), AssignmentTerms(attempt=1))

    assert await fit_in_frame(deps, link, task, assign, limit=1) is assign  # A limit it ignores.


async def test_an_assignment_that_would_not_fit_a_frame_is_sent_fresh_and_says_why() -> None:
    deps, link, task, ref = await _scene(remote=True)
    _plan, assign = await _assign_with(deps, link, task, ref)

    sent = await fit_in_frame(deps, link, task, assign, limit=500)

    assert sent.handoff is None and sent.resume_from is None
    assert sent.task_id == task.id
    assert await _fresh_reasons(deps) == [REASON_TOO_LARGE]
