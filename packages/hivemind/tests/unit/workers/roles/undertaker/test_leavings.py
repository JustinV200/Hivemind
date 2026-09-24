"""Tests for hivemind.workers.roles.undertaker.leavings: LeavingsStoreRemover over a real ledger.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/undertaker/leavings.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.undertaker.leavings for the module under test.
    - hivemind.cell.leavings.store_memory for InMemoryLeavingsStore, the real store used here.
"""

from __future__ import annotations

from builders.cells import make_identity, make_leaving

from hivemind.cell import CellIdentity
from hivemind.cell.leavings import InMemoryLeavingsStore, Leaving
from hivemind.pheromone import CellEvent, MemoryPheromoneTrail, TrailQuery
from hivemind.workers.roles.undertaker import DEFAULT_REMOVAL_EVENT, LeavingsStoreRemover
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_event_id


async def _seed(
    store: InMemoryLeavingsStore, clock: FakeClock, identity: CellIdentity, leaving: Leaving
) -> None:
    """Record one `cell.left` row through the store's own atomic write."""
    await store.record_leaving(
        leaving,
        CellEvent(
            id=new_event_id(clock),
            hive_id=identity.hive_id,
            node_id=identity.node_id,
            actor=identity.actor,
            at=clock.now(),
            kind="cell.left",
            subject_id=leaving.cell_id,
            payload={"lease_id": leaving.lease_id, "path": str(leaving.path)},
        ),
    )


async def test_mark_cell_removed_marks_every_active_row_and_records_one_event_each() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryLeavingsStore(trail)
    identity = make_identity(clock=clock)
    cell_id = new_cell_id(clock)
    first = make_leaving(clock=clock, cell_id=cell_id, path="/outside/one.txt")
    second = make_leaving(clock=clock, cell_id=cell_id, path="/outside/two.txt")
    await _seed(store, clock, identity, first)
    await _seed(store, clock, identity, second)

    removed = await LeavingsStoreRemover(store, clock, identity).mark_cell_removed(
        cell_id, clock.now()
    )

    assert removed == 2
    assert await store.list_leavings(cell_id) == ()  # No active rows left at all.
    events = await trail.query(TrailQuery(subject_id=cell_id))
    kinds = [event.kind for event in events]
    assert kinds.count("cell.leaving_removed") == 2
    marked = next(event for event in events if event.kind == "cell.leaving_removed")
    assert marked.payload["reason"] == DEFAULT_REMOVAL_EVENT


async def test_mark_cell_removed_is_idempotent_for_a_cell_with_nothing_left() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryLeavingsStore(trail)
    identity = make_identity(clock=clock)
    cell_id = new_cell_id(clock)
    await _seed(store, clock, identity, make_leaving(clock=clock, cell_id=cell_id))
    remover = LeavingsStoreRemover(store, clock, identity)

    assert await remover.mark_cell_removed(cell_id, clock.now()) == 1
    # A second pass finds no active row, so it writes nothing and reports nothing removed: the
    # Undertaker's own destroy path may run twice for one Cell (CellBackend.destroy is idempotent).
    assert await remover.mark_cell_removed(cell_id, clock.now()) == 0


async def test_mark_cell_removed_never_touches_another_cells_rows() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryLeavingsStore(trail)
    identity = make_identity(clock=clock)
    destroyed = new_cell_id(clock)
    survivor = new_cell_id(clock)
    await _seed(store, clock, identity, make_leaving(clock=clock, cell_id=destroyed))
    await _seed(store, clock, identity, make_leaving(clock=clock, cell_id=survivor))

    await LeavingsStoreRemover(store, clock, identity).mark_cell_removed(destroyed, clock.now())

    assert len(await store.list_leavings(survivor)) == 1
