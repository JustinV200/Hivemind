"""Read and purge one Bee Bread kind in the in-memory store: the retention window's own quarter.

Most Bee Bread entries live until a House Bee sweep folds them into a summary or ripens them into
Honey (the cold tier). A few kinds live only for a window: a watching Warden's observations
(roadmap step 11.10, ADR-0054) describe the operator's own machine, so they are kept
`[supervision.watch] retention_s` and gone after, or at once when the Patrol that covered them
found nothing notable. `_RetentionMemoryStore` is the mixin `InMemoryMemoryStore` inherits for the
two calls that need: reading one kind in a time range without loading the others' payloads, and
deleting one kind older than a cutoff. It is split out only for codingrules 5.1's class-length
limit, the way `_NightVeilMemoryStore` is.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `hivemind.memory.store.
    memory.InMemoryMemoryStore`; the SQLite counterpart is `hivemind.memory.store.sqlite.
    retention`. Calls into hivemind.cell (HoneyClearance) and hivemind.memory (bee_bread.entry,
    taint) only.

Key invariants:
    - A purge touches exactly one kind and only entries strictly older than the cutoff; every other
      kind, and every pin, note, Handoff, episode and Cell Wax row, is left as it was.
    - A read returns only entries within the reader's allowance and never a TAINTED one, the same
      two filters `list_bee_bread_between` applies.
    - No event is recorded for a purge: it is a retention rule, not an audit fact about one row
      (`purge_episodes_before` makes the same trade).

See Also:
    - hivemind.memory.store.protocol for `MemoryStore.list_bee_bread_of_kind` and
      `purge_bee_bread_before`, the contract both stores keep.
    - hivemind.memory.store.sqlite.retention for the durable half.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.memory.bee_bread.entry import BeeBreadEntry, BeeBreadEntryKind
from hivemind.memory.taint import is_refused

# No __all__: private to this package, like hivemind.memory.store.night_veil.


class _RetentionMemoryStore:
    """The retention window's quarter of InMemoryMemoryStore, split out for codingrules 5.1.

    Reads `self._bee_bread` and `self._lock`, set by `InMemoryMemoryStore.__init__`; never
    instantiated on its own. The annotations declare that shared state for mypy.
    """

    _bee_bread: dict[str, BeeBreadEntry]
    _lock: asyncio.Lock

    async def list_bee_bread_of_kind(
        self, kind: BeeBreadEntryKind, start: datetime, end: datetime, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return entries of `kind` in `[start, end]`; see `MemoryStore.list_bee_bread_of_kind`."""
        async with self._lock:
            entries = list(self._bee_bread.values())
        matches = [
            entry
            for entry in entries
            if entry.kind is kind
            and start <= entry.created_at <= end
            and entry.clearance.rank <= allowance.rank
            and not is_refused(entry.tainted)
        ]
        matches.sort(key=lambda entry: (entry.created_at, entry.id))
        return tuple(matches)

    async def purge_bee_bread_before(self, kind: BeeBreadEntryKind, cutoff: datetime) -> int:
        """Delete `kind` entries older than `cutoff`; see `MemoryStore.purge_bee_bread_before`."""
        async with self._lock:
            stale = [
                entry_id
                for entry_id, entry in self._bee_bread.items()
                if entry.kind is kind and entry.created_at < cutoff
            ]
            for entry_id in stale:
                del self._bee_bread[entry_id]
        return len(stale)
