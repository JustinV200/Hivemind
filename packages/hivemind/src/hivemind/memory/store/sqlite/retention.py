"""Read and purge one Bee Bread kind in the SQLite memory tables: the retention window's quarter.

The SQLite half of `hivemind.memory.store.retention` (that module's docstring says why some Bee
Bread kinds live only for a window): `_SqliteRetentionStore` is the mixin `SqliteMemoryStore`
inherits for reading one kind in a time range (a Patrol's observations, without loading the
transcripts beside them) and for deleting one kind older than a cutoff. The SQL itself is in
`hivemind.memory.store.sqlite.bee_bread`, beside the table's other statements; this class only
runs it on the store's connection thread under the store's lock.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `hivemind.memory.store.
    sqlite.store.SqliteMemoryStore`. Calls into hivemind.cell (HoneyClearance),
    hivemind.common.sqlite (ConnectionThread), hivemind.memory.bee_bread and
    hivemind.memory.store.sqlite.bee_bread only.

Key invariants:
    - A purge deletes one kind and only entries strictly older than the cutoff.
    - A read never returns a TAINTED entry or one above the reader's allowance, exactly as
      `list_bee_bread_between` does.

See Also:
    - hivemind.memory.store.protocol for the contract both stores keep.
    - hivemind.memory.store.retention for the in-memory half.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import ConnectionThread
from hivemind.memory.bee_bread.entry import BeeBreadEntry, BeeBreadEntryKind
from hivemind.memory.store.sqlite import bee_bread

# No __all__: private to this package, like hivemind.memory.store.sqlite.night_veil.


class _SqliteRetentionStore:
    """The retention window's quarter of SqliteMemoryStore, split out for codingrules 5.1.

    Reads `self._connection`, `self._thread` and `self._lock`, set by `SqliteMemoryStore.
    __init__`; never instantiated on its own. The annotations declare that shared state for mypy.
    """

    _connection: sqlite3.Connection
    _thread: ConnectionThread
    _lock: asyncio.Lock

    async def list_bee_bread_of_kind(
        self, kind: BeeBreadEntryKind, start: datetime, end: datetime, allowance: HoneyClearance
    ) -> tuple[BeeBreadEntry, ...]:
        """Return entries of `kind` in `[start, end]`; see `MemoryStore.list_bee_bread_of_kind`."""
        async with self._lock:
            # Blocking: one indexed SELECT (the created_at index) on the store's own thread.
            rows = await self._thread.run(
                bee_bread.select_kind_rows, self._connection, kind, (start, end), allowance
            )
        return tuple(BeeBreadEntry.model_validate_json(row["body"]) for row in rows)

    async def purge_bee_bread_before(self, kind: BeeBreadEntryKind, cutoff: datetime) -> int:
        """Delete `kind` entries older than `cutoff`; see `MemoryStore.purge_bee_bread_before`."""
        async with self._lock:
            # Blocking: one DELETE in one transaction; the store's retention DELETE for Bee Bread.
            return await self._thread.run(
                bee_bread.purge_kind_transaction, self._connection, kind, cutoff
            )
