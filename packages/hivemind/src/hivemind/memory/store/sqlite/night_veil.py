"""Purge the SQLite memory tables of a Night Veil Cell: the store's one exception to append-only.

The SQLite half of `hivemind.memory.store.night_veil` (that module's docstring says what goes and
why): `_SqliteNightVeilStore` is the mixin `SqliteMemoryStore` inherits, and
`purge_night_veil_transaction` is the one transaction it runs on the store's connection thread.
Each table gets one constant `DELETE` that matches a row when its key column is one of the ids or
its stored JSON body contains one; the ids travel as one JSON array parameter read back with
`json_each`, so the statement never changes with their number. A row's taint label is a column of
the row (`taint_state`, migration 0004), so it goes with it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Mixed into `hivemind.memory.store.
    sqlite.store.SqliteMemoryStore`. Calls into `hivemind.common.sqlite` (ConnectionThread,
    transaction) only.

Key invariants:
    - These five statements are the memory tables' only `DELETE`s outside the retention and
      eviction paths `records` documents, and run only through `MemoryStore.purge_night_veil`,
      which only the Night Veil teardown purge calls.
    - All five run in one transaction: a purge that fails partway removes nothing.
    - `memory_pins` is never touched: a pin is the human's own, kept on purpose.
    - No event is recorded: the purge's own `cell.purged` counts what went.

See Also:
    - hivemind.memory.store.night_veil for the in-memory half and the rule both follow.
    - hivemind.memory.store.migrations for the columns matched here.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from hivemind.common.sqlite import ConnectionThread, transaction

# No __all__: private to this package, like hivemind.memory.store.sqlite.records.

# Each table's key column, then its body: a row about the Cell names one of the ids in either.
_PURGE_SQL = (
    "DELETE FROM memory_episodes WHERE EXISTS (SELECT 1 FROM json_each(?) AS ids "
    "WHERE memory_episodes.principal = ids.value OR instr(memory_episodes.body, ids.value) > 0)",
    "DELETE FROM memory_handoffs WHERE EXISTS (SELECT 1 FROM json_each(?) AS ids "
    "WHERE memory_handoffs.task_id = ids.value OR instr(memory_handoffs.body, ids.value) > 0)",
    "DELETE FROM memory_bee_bread WHERE EXISTS (SELECT 1 FROM json_each(?) AS ids "
    "WHERE memory_bee_bread.task_id = ids.value OR instr(memory_bee_bread.body, ids.value) > 0)",
    "DELETE FROM memory_notes WHERE EXISTS (SELECT 1 FROM json_each(?) AS ids "
    "WHERE memory_notes.author = ids.value OR instr(memory_notes.body, ids.value) > 0)",
    "DELETE FROM memory_cell_wax WHERE EXISTS (SELECT 1 FROM json_each(?) AS ids "
    "WHERE memory_cell_wax.cell_id = ids.value OR instr(memory_cell_wax.body, ids.value) > 0)",
)


class _SqliteNightVeilStore:
    """The Night Veil purge's quarter of SqliteMemoryStore, split out for codingrules 5.1.

    Reads `self._connection`, `self._thread` and `self._lock`, set by `SqliteMemoryStore.
    __init__`; never instantiated on its own. The annotations declare that shared state for mypy.
    """

    _connection: sqlite3.Connection
    _thread: ConnectionThread
    _lock: asyncio.Lock

    async def purge_night_veil(self, ids: frozenset[str]) -> int:
        """Remove every row naming one of `ids`; see `MemoryStore.purge_night_veil`."""
        if not ids:
            return 0  # Nothing names no one: skip the transaction altogether.
        async with self._lock:
            # Blocking: five indexed-or-scanned DELETEs in one transaction; a Hive's memory tables
            # are bounded by their retention windows, so even the body scan stays small.
            return await self._thread.run(purge_night_veil_transaction, self._connection, ids)


def purge_night_veil_transaction(connection: sqlite3.Connection, ids: frozenset[str]) -> int:
    """Delete every memory row naming one of `ids`, in one transaction; return how many."""
    parameter = json.dumps(sorted(ids))
    with transaction(connection):
        return sum(connection.execute(sql, (parameter,)).rowcount for sql in _PURGE_SQL)
