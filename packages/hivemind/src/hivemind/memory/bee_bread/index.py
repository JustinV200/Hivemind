"""Define BeeBread: a lookup-only index over the warm memory tier.

Bee Bread is "lookup by id, time, task; no search" (codingrules section 8.9): unlike Honey (the
cold tier, phase 7), which gets full-text and semantic search, the warm tier only ever answers "get
me the thing I already know the id, task or time range of". `BeeBread` is that lookup surface: a
thin wrapper over `hivemind.memory.store.protocol.MemoryStore`'s own `*_bee_bread_*` methods, adding
nothing of its own beyond the three lookup shapes the roadmap names (`by_id`, `by_task`,
`between`). Every method takes the reader's `HoneyClearance` allowance and returns only entries at
or below it, exactly like every other memory-tier reader (codingrules section 8.9).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Constructed by whichever composition
    root wires up warm-tier lookups for a Queen, a Warden or the Observation Hive (a later roadmap
    step). Calls into hivemind.cell (HoneyClearance), hivemind.memory.bee_bread.entry
    (BeeBreadEntry) and, under TYPE_CHECKING only, hivemind.memory.store.protocol (see the module's
    "Key invariants" for why).

Key invariants:
    - The `MemoryStore` import below is TYPE_CHECKING-only: `hivemind.memory.store.protocol`
      imports `BeeBreadEntry` from this package (also TYPE_CHECKING-only, for the same reason) to
      shape its own `*_bee_bread_*` Protocol methods, so a real, eager import here would close that
      cycle. `from __future__ import annotations` already makes `store: MemoryStore` a string at
      runtime, so `BeeBread.__init__` never needs to resolve the name; only a type checker does.
    - BeeBread never filters or sorts beyond what its one MemoryStore call already does: every
      ordering and clearance-filtering decision is the store's own (codingrules section 12).

See Also:
    - .claude/codingrules.md section 8.9 for "lookup by id, time, task; no search".
    - hivemind.memory.bee_bread.entry for BeeBreadEntry, the row every lookup returns.
    - hivemind.memory.bee_bread.deposit for the write paths this index's rows come from.
    - hivemind.memory.store.protocol for the MemoryStore methods this class wraps.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.memory.bee_bread.entry import BeeBreadEntry
from waggle.ids import EventId, TaskId

if TYPE_CHECKING:
    # Type-checking only: see the module docstring's "Key invariants" for why a real import here
    # would be circular (hivemind.memory.store.protocol imports BeeBreadEntry from this package).
    from hivemind.memory.store.protocol import MemoryStore

__all__ = ["BeeBread"]


class BeeBread:
    """A lookup-only view over the warm memory tier's stored entries."""

    def __init__(self, store: MemoryStore) -> None:
        """Wrap `store` for warm-tier lookups.

        Args:
            store: Where every BeeBreadEntry this index reads is persisted.
        """
        self._store = store

    async def by_id(self, entry_id: EventId, allowance: HoneyClearance) -> BeeBreadEntry:
        """Return the entry with id `entry_id`.

        Args:
            entry_id: The entry's own id.
            allowance: The reader's clearance ceiling.

        Returns:
            The matching BeeBreadEntry.

        Raises:
            hivemind.memory.errors.BeeBreadEntryNotFoundError: No entry with `entry_id` exists.
            hivemind.memory.errors.ClearanceError: The entry's clearance is above `allowance`.
        """
        return await self._store.get_bee_bread_entry(entry_id, allowance)

    async def by_task(
        self, task_id: TaskId, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return every entry concerning `task_id`, within `allowance`.

        Args:
            task_id: The task to look up entries for.
            allowance: The reader's clearance ceiling.

        Returns:
            Matching entries, oldest first.
        """
        return await self._store.list_bee_bread_by_task(task_id, allowance)

    async def between(
        self, start: datetime, end: datetime, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return every entry written in `[start, end]`, within `allowance`.

        Args:
            start: Inclusive lower bound.
            end: Inclusive upper bound.
            allowance: The reader's clearance ceiling.

        Returns:
            Matching entries, oldest first.
        """
        return await self._store.list_bee_bread_between(start, end, allowance)
