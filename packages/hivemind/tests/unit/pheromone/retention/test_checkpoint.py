"""Tests for hivemind.pheromone.retention.checkpoint: what a restarted Queen keeps of a Cell.

Both stores keep one checkpoint per Cell until it is forgotten (the SQLite one across a reopened
connection, as a restarted Queen opens it). Through `EphemeralSegments`, a held Night Veil Cell's
Capping counts and member ids are checkpointed as its records arrive, and nothing else of them; a
fresh store (a restarted Queen) recalls both, counts on from them without counting an event its
Warden ships again, and hands the whole tally to the purge, forgetting the checkpoint.

Fits into the Hive:
    Mirrors src/hivemind/pheromone/retention/checkpoint.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.pheromone.retention.checkpoint for the module under test.
    - hivemind.pheromone.retention.segments for where it is written and read.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import JsonValue

from hivemind.common.sqlite import connect
from hivemind.pheromone import CappingEvent, PheromoneEvent, TaskEvent, TrailSegment
from hivemind.pheromone.retention import (
    CellCheckpoint,
    EphemeralSegments,
    LazySqliteCheckpoints,
    MemoryCheckpoints,
    NightVeilCheckpoints,
    TierCount,
    TierTally,
    Veiling,
)
from hivemind.pheromone.trail.sqlite import apply_pheromone_migrations
from waggle.clock import FakeClock
from waggle.ids import NodeId, new_cell_id, new_event_id, new_hive_id, new_node_id

_TIER = "SCRATCH_WRITE"
_TASK = "task_01HZZZZZZZZZZZZZZZZZZZZZZZ"
_WARDEN = "warden_01HZZZZZZZZZZZZZZZZZZZZZZZ"
_APPROVED = ("capping.proposed", "capping.capped")
_PROPOSAL_ONE = "msg_01HZZZZZZZZZZZZZZZZZZZZZZ1"  # A Capping proposal is a message id.
_PROPOSAL_TWO = "msg_01HZZZZZZZZZZZZZZZZZZZZZZ2"
_REJECTED = ("capping.proposed", "capping.rejected")


def _capping(clock: FakeClock, node: NodeId, proposal: str, kind: str) -> PheromoneEvent:
    """One Capping event about `proposal`, naming its tier as proposed/capped/rejected do."""
    payload: dict[str, JsonValue] = {} if kind == "capping.rolled_back" else {"tier": _TIER}
    return CappingEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=node,
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=proposal,
        payload=payload,
    )


def _segment(node: NodeId, *events: PheromoneEvent) -> TrailSegment:
    """What the Cell's Warden ships: its own node's events."""
    return TrailSegment(node_id=node, exported_at=events[-1].at, events=events)


async def _check_store(store: NightVeilCheckpoints) -> None:
    """One store's contract: save replaces, recall reads, forget removes and is idempotent."""
    cell = new_cell_id(FakeClock())
    tally = TierTally(tier=_TIER, approved=2, rejected=1, rolled_back=0)
    checkpoint = CellCheckpoint(counts=(tally,), members=frozenset({_TASK}))
    assert await store.recall(cell) is None
    await store.save(cell, CellCheckpoint())
    await store.save(cell, checkpoint)
    assert await store.recall(cell) == checkpoint
    await store.forget(cell)
    await store.forget(cell)
    assert await store.recall(cell) is None


async def test_the_memory_store_keeps_one_checkpoint_per_cell_until_forgotten() -> None:
    await _check_store(MemoryCheckpoints())


async def test_the_sqlite_store_keeps_one_checkpoint_per_cell_across_a_reopening(
    tmp_path: Path,
) -> None:
    database, clock = tmp_path / "hive.sqlite3", FakeClock()
    apply_pheromone_migrations(connect(database), clock)
    await _check_store(LazySqliteCheckpoints(database, clock))
    cell = new_cell_id(clock)
    await LazySqliteCheckpoints(database, clock).save(cell, CellCheckpoint(members=frozenset()))

    # A restarted Queen opens the file again and finds it.
    assert await LazySqliteCheckpoints(database, clock).recall(cell) == CellCheckpoint()


async def test_a_held_cells_counts_and_ids_are_checkpointed_as_its_records_arrive() -> None:
    clock, store = FakeClock(), MemoryCheckpoints()
    segments, cell, node = EphemeralSegments(clock, store), new_cell_id(clock), new_node_id(clock)
    segments.open(cell)
    segments.bind(_TASK, cell)
    segments.file(_WARDEN, cell)
    proposed, capped = (_capping(clock, node, _PROPOSAL_ONE, k) for k in _APPROVED)
    await segments.merge(cell, _segment(node, proposed, capped))
    await segments.merge(cell, _segment(node, proposed, capped))  # Shipped twice: once counted.

    [checkpoint] = store.rows().values()

    assert [tally.count() for tally in checkpoint.counts] == [TierCount(_TIER, 1, 0, 0)]
    assert checkpoint.members == frozenset({_TASK, _WARDEN, node})
    assert checkpoint.counted_through == capped.at


async def test_a_restarted_queen_counts_on_from_the_checkpoint_and_the_purge_forgets_it() -> None:
    clock, store = FakeClock(), MemoryCheckpoints()
    cell, node = new_cell_id(clock), new_node_id(clock)
    before = EphemeralSegments(clock, store)
    before.open(cell)
    before.bind(_TASK, cell)
    first = [_capping(clock, node, _PROPOSAL_ONE, kind) for kind in _APPROVED]
    await before.merge(cell, _segment(node, *first))

    # The Queen stops; the Cell lives on, and a new Queen holds it again.
    after = EphemeralSegments(clock, store)
    after.open(cell)
    await after.recall(cell)
    late = TaskEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind="task.progressed",
        subject_id=_TASK,
        payload={},
    )
    assert after.veiling(late) == Veiling(cell_id=cell, held=True)  # Its task is its own again.
    # Its Warden ships the event at its cursor again, then a rejected proposal.
    second = [_capping(clock, node, _PROPOSAL_TWO, kind) for kind in _REJECTED]
    await after.merge(cell, _segment(node, first[-1], *second))

    taken = await after.take(cell)

    assert taken.counts == (TierCount(_TIER, 1, 1, 0),)
    assert _TASK in taken.members
    assert store.rows() == {}


async def test_a_cell_never_checkpointed_is_taken_with_nothing_counted() -> None:
    clock = FakeClock()
    segments, cell = EphemeralSegments(clock, MemoryCheckpoints()), new_cell_id(clock)

    taken = await segments.take(cell)

    assert taken.counts == () and taken.members == frozenset()
