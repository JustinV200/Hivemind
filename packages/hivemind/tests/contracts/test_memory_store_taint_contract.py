"""Contract suite for MemoryStore's taint half: one contract, over both memory stores.

Roadmap step 10.6d: the memory tables are a `TaintLedger`. Each test states one clause of that
contract (a scope's scan, one item's read, the labelled write and its transition check) or of the
refusals every other read and insert now makes, and runs against both implementations:
`InMemoryMemoryStore` over `MemoryPheromoneTrail` and `SqliteMemoryStore` over
`SqlitePheromoneTrail` on one tmp_path file. Split by feature from test_memory_store_contract.py.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Contract for hivemind.memory.store.protocol.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.memory.store.protocol for MemoryStore's _TaintStore half.
    - hivemind.memory.taint.ledger for the TaintLedger contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.memory import make_bee_bread_entry, make_episode, make_handoff

from hivemind.cell import HoneyClearance
from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.memory import (
    MemoryContext,
    MemoryIdentity,
    read_handoff,
    record_episode,
    write_checkpoint,
)
from hivemind.memory.errors import (
    InvalidTaintTransitionError,
    TaintedMemoryError,
    TaintTargetNotFoundError,
)
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.memory.store.protocol import MemoryStore
from hivemind.memory.store.sqlite import SqliteMemoryStore
from hivemind.memory.taint import (
    TaintedKind,
    TaintMarker,
    TaintScope,
    TaintSource,
    TaintState,
    TaintTarget,
)
from hivemind.pheromone import (
    MemoryEvent,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
)
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id, new_task_id, new_worker_id
from waggle.messages import HandoffRef

_C2 = HoneyClearance.C2


@dataclass(frozen=True, slots=True)
class _World:
    """A store, its trail and the clock both share."""

    store: MemoryStore
    trail: PheromoneTrail
    clock: FakeClock

    def ctx(self) -> MemoryContext:
        identity = MemoryIdentity(
            hive_id=new_hive_id(self.clock), node_id=new_node_id(self.clock), actor="system"
        )
        return MemoryContext(store=self.store, identity=identity, clock=self.clock)


@dataclass(frozen=True, slots=True)
class _Seeded:
    """One bee's checkpoint (a Handoff and its Bee Bread index) and one episode record."""

    bee: str
    task_id: str
    ref: HandoffRef
    handoff: TaintTarget
    episode: TaintTarget
    entry: TaintTarget


@pytest.fixture(params=("memory", "sqlite"))
async def world(request: pytest.FixtureRequest, tmp_path: Path) -> _World:
    """A (store, trail, clock) of the parametrised kind; SQLite on one tmp_path file."""
    clock = FakeClock()
    if request.param == "memory":
        trail = MemoryPheromoneTrail(clock)
        return _World(store=InMemoryMemoryStore(trail), trail=trail, clock=clock)
    db_path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db_path), clock)
    sqlite_store = await SqliteMemoryStore.create(connect(db_path), clock)
    return _World(store=sqlite_store, trail=sqlite_trail, clock=clock)


def _event(world: _World, target: TaintTarget, kind: str) -> MemoryEvent:
    return MemoryEvent(
        id=new_event_id(world.clock),
        hive_id=new_hive_id(world.clock),
        node_id=new_node_id(world.clock),
        at=world.clock.now(),
        actor="system",
        kind=kind,
        subject_id=target.item_id,
        payload={"item_kind": target.kind.value},
    )


async def _taint(world: _World, target: TaintTarget) -> TaintMarker:
    """Write a TAINTED label on `target` with its memory.tainted event; return the label."""
    event = _event(world, target, "memory.tainted")
    marker = TaintMarker(
        state=TaintState.TAINTED,
        source=TaintSource.ISOLATION,
        reason="Isolated on a Guard report.",
        event_id=event.id,
        at=event.at,
    )
    await world.store.write_taint(target, marker, event)
    return marker


async def _clear(world: _World, target: TaintTarget, tainted: TaintMarker) -> TaintMarker:
    """Write the CLEARED label following `tainted` with its memory.taint_cleared event."""
    event = _event(world, target, "memory.taint_cleared")
    marker = TaintMarker(
        state=TaintState.CLEARED,
        source=tainted.source,
        reason=tainted.reason,
        event_id=tainted.event_id,
        at=tainted.at,
        cleared_event_id=event.id,
        cleared_at=event.at,
    )
    await world.store.write_taint(target, marker, event)
    return marker


async def _seed(world: _World) -> _Seeded:
    bee, task_id = new_worker_id(world.clock), new_task_id(world.clock)
    handoff = make_handoff(written_by=bee, task_id=task_id, goal="Publish the weekly report.")
    ref = await write_checkpoint(handoff, task_id, world.ctx())
    record = make_episode(world.clock, principal=bee, decision="Retry the upload.")
    await record_episode(record, world.ctx())
    [entry] = await world.store.list_bee_bread_by_task(task_id, _C2)
    return _Seeded(
        bee=bee,
        task_id=task_id,
        ref=ref,
        handoff=TaintTarget(kind=TaintedKind.HANDOFF, item_id=ref.event_id),
        episode=TaintTarget(kind=TaintedKind.EPISODE, item_id=record.id),
        entry=TaintTarget(kind=TaintedKind.BEE_BREAD, item_id=entry.id),
    )


# ──────────────────────────────────────────────────────────────────────────────
# The ledger: find, read, write
# ──────────────────────────────────────────────────────────────────────────────


async def test_find_taintable_returns_what_a_scope_covers_and_not_what_is_tainted(
    world: _World,
) -> None:
    seeded = await _seed(world)
    scope = TaintScope(
        authors=frozenset({seeded.bee}),
        task_ids=frozenset({seeded.task_id}),
        since=world.clock.now(),
    )

    before = await world.store.find_taintable(scope)
    await _taint(world, seeded.handoff)
    after = await world.store.find_taintable(scope)

    assert set(before) == {seeded.handoff, seeded.episode, seeded.entry}
    assert set(after) == {seeded.episode, seeded.entry}


async def test_find_taintable_honours_the_scopes_kinds_and_moment(world: _World) -> None:
    seeded = await _seed(world)
    world.clock.advance(5.0)
    later = TaintScope(authors=frozenset({seeded.bee}), since=world.clock.now())
    handoffs_only = TaintScope(
        authors=frozenset({seeded.bee}),
        since=world.clock.now().replace(year=2000),
        kinds=frozenset({TaintedKind.HANDOFF}),
    )

    assert await world.store.find_taintable(later) == ()
    assert await world.store.find_taintable(handoffs_only) == (seeded.handoff,)


async def test_read_taintable_shows_the_label_clearance_and_text_but_not_the_author(
    world: _World,
) -> None:
    seeded = await _seed(world)
    marker = await _taint(world, seeded.handoff)

    item = await world.store.read_taintable(seeded.handoff)

    assert item.marker == marker and item.clearance is HoneyClearance.C1
    assert "Publish the weekly report." in item.content
    assert seeded.bee not in item.content


async def test_write_taint_records_its_event_with_the_label(world: _World) -> None:
    seeded = await _seed(world)

    marker = await _taint(world, seeded.episode)

    [event] = await world.trail.query(TrailQuery(kind="memory.tainted"))
    assert event.id == marker.event_id and event.subject_id == seeded.episode.item_id


async def test_an_illegal_edge_is_refused_and_writes_nothing(world: _World) -> None:
    seeded = await _seed(world)
    await _taint(world, seeded.handoff)

    with pytest.raises(InvalidTaintTransitionError):
        await _taint(world, seeded.handoff)  # TAINTED -> TAINTED

    assert len(await world.trail.query(TrailQuery(kind="memory.tainted"))) == 1


async def test_a_cleared_item_can_be_tainted_again_by_a_later_incident(world: _World) -> None:
    seeded = await _seed(world)
    first = await _taint(world, seeded.handoff)
    await _clear(world, seeded.handoff, first)

    again = await _taint(world, seeded.handoff)

    assert (await world.store.read_taintable(seeded.handoff)).marker == again


async def test_an_unknown_or_foreign_target_is_not_found(world: _World) -> None:
    missing = TaintTarget(kind=TaintedKind.HANDOFF, item_id=new_event_id(world.clock))
    nectar = TaintTarget(kind=TaintedKind.NECTAR, item_id=new_event_id(world.clock))

    with pytest.raises(TaintTargetNotFoundError):
        await world.store.read_taintable(missing)
    with pytest.raises(TaintTargetNotFoundError):
        await _taint(world, nectar)


# ──────────────────────────────────────────────────────────────────────────────
# Every other read refuses a TAINTED item; every insert refuses a labelled one
# ──────────────────────────────────────────────────────────────────────────────


async def test_reads_that_could_feed_a_prompt_refuse_tainted_items(world: _World) -> None:
    seeded = await _seed(world)
    for target in (seeded.handoff, seeded.episode, seeded.entry):
        await _taint(world, target)

    assert await world.store.list_episodes(seeded.bee, _C2, 10) == ()
    assert await world.store.list_bee_bread_by_task(seeded.task_id, _C2) == ()
    assert await world.store.list_bee_bread_between(world.clock.now(), world.clock.now(), _C2) == ()
    with pytest.raises(TaintedMemoryError):
        await world.store.get_bee_bread_entry(seeded.entry.item_id, _C2)
    with pytest.raises(TaintedMemoryError):
        await read_handoff(world.store, seeded.ref, _C2)


async def test_a_cleared_item_is_readable_again_and_keeps_its_history(world: _World) -> None:
    seeded = await _seed(world)
    tainted = await _taint(world, seeded.episode)
    cleared = await _clear(world, seeded.episode, tainted)
    handoff_label = await _taint(world, seeded.handoff)
    await _clear(world, seeded.handoff, handoff_label)

    [record] = await world.store.list_episodes(seeded.bee, _C2, 10)
    handoff = await read_handoff(world.store, seeded.ref, _C2)

    assert record.tainted == cleared
    assert handoff.tainted is not None and handoff.tainted.state is TaintState.CLEARED


async def test_every_insert_refuses_an_item_that_arrives_labelled(world: _World) -> None:
    seeded = await _seed(world)
    marker = await _taint(world, seeded.handoff)
    ctx = world.ctx()

    with pytest.raises(InvariantViolationError, match="arrived labelled"):
        await write_checkpoint(make_handoff(tainted=marker), None, ctx)
    with pytest.raises(InvariantViolationError, match="arrived labelled"):
        await record_episode(make_episode(world.clock, tainted=marker), ctx)
    with pytest.raises(InvariantViolationError, match="arrived labelled"):
        await world.store.add_bee_bread_entry(
            make_bee_bread_entry(world.clock, tainted=marker),
            _event(world, seeded.entry, "memory.bee_bread_deposited"),
        )
