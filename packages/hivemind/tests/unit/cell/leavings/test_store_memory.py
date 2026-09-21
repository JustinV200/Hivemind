"""Tests for hivemind.cell.leavings.store_memory: InMemoryLeavingsStore's own atomicity behaviour.

InMemoryLeavingsStore's ordinary behaviour is covered by
tests/contracts/test_leavings_store_contract.py, parametrised over it and SqliteLeavingsStore
together. This module covers what is specific to the in-memory adapter: that a trail record
failure leaves the dict exactly as it was before the call.

Fits into the Hive:
    Mirrors src/hivemind/cell/leavings/store_memory.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.leavings.store_memory for the module under test.
    - tests/contracts/test_leavings_store_contract.py for the shared LeavingsStore behaviour.
"""

from __future__ import annotations

import pytest
from builders.cells import make_leaving

from hivemind.cell.errors import LeavingNotFoundError
from hivemind.cell.leavings.store_memory import InMemoryLeavingsStore
from hivemind.pheromone import CellEvent, DuplicateEventError
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import EventId, new_event_id, new_hive_id, new_node_id


def _event(clock: FakeClock, cell_id: str, event_id: EventId, kind: str) -> CellEvent:
    """Build a well-formed CellEvent with an explicit `event_id`, so a caller can collide it."""
    return CellEvent(
        id=event_id,
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=cell_id,
        payload={},
    )


async def test_record_leaving_with_a_pre_recorded_event_id_leaves_the_store_empty() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    store = InMemoryLeavingsStore(trail)
    leaving = make_leaving(clock=clock)
    event_id = new_event_id(clock)
    # Records the same id directly on the trail first, bypassing the store, so the store's own
    # record_leaving call is guaranteed to hit DuplicateEventError on this event.
    await trail.record(_event(clock, leaving.cell_id, event_id, "cell.left"))

    with pytest.raises(DuplicateEventError):
        await store.record_leaving(leaving, _event(clock, leaving.cell_id, event_id, "cell.left"))

    with pytest.raises(LeavingNotFoundError):
        await store.get_leaving(leaving.cell_id, leaving.path)
