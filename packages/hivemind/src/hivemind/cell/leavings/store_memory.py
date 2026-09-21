"""Provide InMemoryLeavingsStore: an in-process LeavingsStore for tests and demos.

Mirrors `hivemind.brood_chamber.store.memory.MemoryTaskStore`'s own shape (codingrules 14.4:
"fakes live in src/ beside their Protocol"): one dict keyed by `(cell_id, path)`, guarded by a
lock, gone when the process exits. `hivemind.cell.leavings.store_sqlite.SqliteLeavingsStore` is the
durable implementation a real Hive uses; this one serves `pollen`, `hive doctor`, demo paths and
every unit test that does not need a real SQLite file (including `cli/readback/cells.py`'s own
throwaway use for `hive cells list`, which never leases and so never needs a durable ledger).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell.leavings`. Used by
    tests, demos, and any composition root that wants a LeavingsStore-shaped collaborator without a
    database file. Calls into hivemind.cell (errors, leavings.model, leavings.store_protocol),
    hivemind.common.errors and hivemind.pheromone (`CellEvent`, `PheromoneTrail`) only.

Key invariants:
    - Every method here mirrors `LeavingsStore`'s own contract exactly (upserting
      `record_leaving`, which keeps an existing active row's own `prior`; non-idempotent
      `mark_removed`); the contract suite (`tests/contracts/test_leavings_store_contract.py`)
      parametrises over this class and `SqliteLeavingsStore` together.
    - Every method holds `self._lock` for its whole body, so two coroutines can never interleave a
      read with a write, or two writes with each other.

See Also:
    - hivemind.brood_chamber.store.memory for MemoryTaskStore, the pattern this module follows.
    - hivemind.cell.leavings.store_protocol for LeavingsStore and check_leaving_event, the guard
      every mutation calls first.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from hivemind.cell.errors import LeavingAlreadyRemovedError, LeavingNotFoundError
from hivemind.cell.leavings.model import Leaving
from hivemind.cell.leavings.store_protocol import check_leaving_event
from hivemind.pheromone import CellEvent, PheromoneTrail
from waggle.ids import CellId

__all__ = ["InMemoryLeavingsStore"]


class InMemoryLeavingsStore:
    """An in-process LeavingsStore: one dict keyed by `(cell_id, path)`, guarded by one lock."""

    def __init__(self, trail: PheromoneTrail) -> None:
        """Create an empty store over `trail`.

        Args:
            trail: Where every mutation's event is recorded before the dict changes.
        """
        self._trail = trail
        self._leavings: dict[tuple[CellId, str], Leaving] = {}
        self._lock = asyncio.Lock()

    async def record_leaving(self, leaving: Leaving, event: CellEvent) -> None:
        """Upsert `leaving`'s row and record `event`; see LeavingsStore.record_leaving."""
        check_leaving_event(leaving.cell_id, event)
        key = (leaving.cell_id, str(leaving.path))
        async with self._lock:
            existing = self._leavings.get(key)
            stored = leaving
            if existing is not None and existing.removed_at is None:
                # An active row already covers this path (the same lease noting it twice, or a
                # second goal run while an earlier run's row is still active, coordinator review):
                # every field but `prior` is replaced; `prior` must stay what the path held before
                # any Leaving ever existed there, so `remove` still replays the true original.
                stored = leaving.model_copy(update={"prior": existing.prior})
            await self._trail.record(event)
            self._leavings[key] = stored

    async def get_leaving(self, cell_id: CellId, path: Path) -> Leaving:
        """Return the active Leaving at `(cell_id, path)`; see LeavingsStore.get_leaving."""
        async with self._lock:
            leaving = self._leavings.get((cell_id, str(path)))
        if leaving is None or leaving.removed_at is not None:
            raise LeavingNotFoundError(cell_id, path)
        return leaving

    async def list_leavings(
        self, cell_id: CellId, *, include_removed: bool = False
    ) -> tuple[Leaving, ...]:
        """Return every Leaving for `cell_id`; see LeavingsStore.list_leavings."""
        async with self._lock:
            # Copy while holding the lock; filtering and sorting below never touch shared state.
            leavings = list(self._leavings.values())
        matches = [
            leaving
            for leaving in leavings
            if leaving.cell_id == cell_id and (include_removed or leaving.removed_at is None)
        ]
        matches.sort(key=lambda leaving: (leaving.left_at, str(leaving.path)))
        return tuple(matches)

    async def mark_removed(
        self, cell_id: CellId, path: Path, removed_at: datetime, event: CellEvent
    ) -> Leaving:
        """Mark the active Leaving at `(cell_id, path)` removed; see LeavingsStore.mark_removed."""
        check_leaving_event(cell_id, event)
        key = (cell_id, str(path))
        async with self._lock:
            leaving = self._leavings.get(key)
            if leaving is None:
                raise LeavingNotFoundError(cell_id, path)
            if leaving.removed_at is not None:
                raise LeavingAlreadyRemovedError(cell_id, path)
            await self._trail.record(event)
            updated = leaving.model_copy(update={"removed_at": removed_at})
            self._leavings[key] = updated
            return updated
