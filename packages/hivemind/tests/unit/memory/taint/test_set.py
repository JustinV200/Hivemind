"""Tests for hivemind.memory.taint.set: taint_memory labels a scope's items, each with its event.

Roadmap step 10.6d: one function sets the label, taking a closed TaintSource, and records
`memory.tainted` in the same transaction as the label; it never relabels an item already TAINTED.

Fits into the Hive:
    Mirrors src/hivemind/memory/taint/set.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.taint.set for taint_memory.
    - tests/contracts/test_memory_store_taint_contract.py for the stores' own half.
"""

from __future__ import annotations

from builders.memory import make_episode, make_handoff
from builders.taint import SeamLedger, make_stamp, make_taint_world

from hivemind.memory import record_episode, write_checkpoint
from hivemind.memory.taint import (
    TAINTED_KIND,
    TaintedKind,
    TaintScope,
    TaintSource,
    TaintState,
    TaintTarget,
    taint_memory,
)
from hivemind.pheromone import TrailQuery
from waggle.ids import EventId, new_event_id, new_task_id, new_worker_id


async def test_every_covered_item_is_labelled_with_its_own_memory_tainted_event() -> None:
    world = make_taint_world()
    bee, task = new_worker_id(world.clock), new_task_id(world.clock)
    since = new_event_id(world.clock)
    ref = await write_checkpoint(make_handoff(written_by=bee, task_id=task), task, world.ctx)
    await record_episode(make_episode(world.clock, principal=bee), world.ctx)
    cause = new_event_id(world.clock)

    report = await taint_memory(
        TaintScope.for_bee(bee, since, task_ids=(task,)),
        make_stamp(TaintSource.QUARANTINE, cause_event_id=cause),
        world.ctx,
    )

    kinds = sorted(target.kind.value for target in report.tainted)
    # The Handoff, its Bee Bread index entry, and the episode record.
    assert kinds == ["bee_bread", "episode", "handoff"]
    handoff, _clearance = await world.ctx.store.get_handoff(ref.event_id)
    assert handoff.tainted is not None and handoff.tainted.state is TaintState.TAINTED
    events = await world.trail.query(TrailQuery(kind=TAINTED_KIND))
    assert {event.subject_id for event in events} == {t.item_id for t in report.tainted}
    by_subject = {event.subject_id: event for event in events}
    # The label names the very event that set it, and the event names the cause, never content.
    assert by_subject[ref.event_id].id == handoff.tainted.event_id
    assert by_subject[ref.event_id].payload == {
        "item_kind": "handoff",
        "source": "quarantine",
        "reason": "Quarantined after a Guard report.",
        "cause_event_id": cause,
    }


async def test_an_item_already_tainted_is_never_relabelled() -> None:
    world = make_taint_world()
    bee = new_worker_id(world.clock)
    since = new_event_id(world.clock)
    await write_checkpoint(make_handoff(written_by=bee), None, world.ctx)
    scope = TaintScope.for_bee(bee, since)

    first = await taint_memory(scope, make_stamp(TaintSource.ISOLATION), world.ctx)
    second = await taint_memory(scope, make_stamp(TaintSource.QUARANTINE), world.ctx)

    assert len(first.tainted) == 1 and second.tainted == ()
    [target] = first.tainted
    handoff, _clearance = await world.ctx.store.get_handoff(EventId(target.item_id))
    assert handoff.tainted is not None and handoff.tainted.source is TaintSource.ISOLATION


async def test_items_outside_the_scope_are_left_alone() -> None:
    world = make_taint_world()
    bee, bystander = new_worker_id(world.clock), new_worker_id(world.clock)
    before = await write_checkpoint(make_handoff(written_by=bee), None, world.ctx)
    world.clock.advance(1.0)  # The suspect episode comes a second after that checkpoint.
    since = new_event_id(world.clock)
    elsewhere = await write_checkpoint(make_handoff(written_by=bystander), None, world.ctx)

    report = await taint_memory(TaintScope.for_bee(bee, since), make_stamp(), world.ctx)

    assert report.tainted == ()  # Its only Handoff predates the suspect episode.
    for ref in (before, elsewhere):
        handoff, _clearance = await world.ctx.store.get_handoff(ref.event_id)
        assert handoff.tainted is None


async def test_extra_ledgers_are_labelled_through_the_same_setter() -> None:
    world = make_taint_world()
    bee = new_worker_id(world.clock)
    since = new_event_id(world.clock)
    seam = SeamLedger(world.trail)
    nectar = seam.add(TaintedKind.NECTAR, "A deposited page.", world.clock, author=bee)

    report = await taint_memory(
        TaintScope.for_bee(bee, since),
        make_stamp(TaintSource.GUARD_REPORT),
        world.ctx,
        extra_ledgers=(seam,),
    )

    assert report.tainted == (TaintTarget(kind=TaintedKind.NECTAR, item_id=nectar.item_id),)
    marker = seam.marker_of(nectar)
    assert marker is not None and marker.source is TaintSource.GUARD_REPORT
