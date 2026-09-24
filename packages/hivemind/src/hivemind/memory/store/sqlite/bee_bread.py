"""SQL and transactions for the fifth memory table: memory_bee_bread (roadmap step 4.2).

Split from `hivemind.memory.store.sqlite.records` (which holds the original four tables) purely by
codingrules 5.1's size limit; this module's shape mirrors that one exactly, one table's worth.
Reuses `records._allowed_clearance_values`, the one piece of shared logic between the two, rather
than duplicating it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called only by
    `hivemind.memory.store.sqlite.store.SqliteMemoryStore`, on its `ConnectionThread`. Calls into
    hivemind.cell (HoneyClearance), hivemind.common.sqlite (transaction), hivemind.memory.bee_bread
    (BeeBreadEntry), hivemind.pheromone (insert_event), hivemind.memory.store.sqlite.records and
    sqlite3 only.

Key invariants:
    - `add_entry_transaction` writes its row and its MemoryEvent inside one
      `hivemind.common.sqlite.transaction` block (codingrules section 12).

See Also:
    - hivemind.memory.store.sqlite.store for SqliteMemoryStore, the one caller.
    - hivemind.memory.store.sqlite.records for the original four tables' own SQL, and
      `_allowed_clearance_values`, reused here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import transaction
from hivemind.memory.bee_bread.entry import BeeBreadEntry
from hivemind.memory.store.sqlite.records import UNTAINTED_CLAUSE, _allowed_clearance_values
from hivemind.memory.taint import require_unlabelled
from hivemind.pheromone import MemoryEvent, insert_event

# No __all__: see hivemind.memory.store.sqlite.records's own note; the same reasoning applies here.

_INSERT_SQL = (
    "INSERT INTO memory_bee_bread (id, kind, task_id, clearance, created_at, body) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
_SELECT_BY_ID_SQL = "SELECT body, clearance FROM memory_bee_bread WHERE id = ?"
_SELECT_SQL = "SELECT body FROM memory_bee_bread"
_ORDER_BY = " ORDER BY created_at, id"


def add_entry_transaction(
    connection: sqlite3.Connection, entry: BeeBreadEntry, event: MemoryEvent
) -> None:
    """Insert one bee_bread row then its event, in one transaction; run on the store's thread."""
    require_unlabelled(entry.tainted, entry.id)  # Only the taint ledger writes a label.
    with transaction(connection):
        connection.execute(
            _INSERT_SQL,
            (
                entry.id,
                entry.kind.value,
                entry.task_id,
                entry.clearance.value,
                entry.created_at.isoformat(),
                entry.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def select_by_id_row(connection: sqlite3.Connection, entry_id: str) -> sqlite3.Row | None:
    """Select one bee_bread entry's clearance and body row by id, or None when no row matches."""
    row: sqlite3.Row | None = connection.execute(_SELECT_BY_ID_SQL, (entry_id,)).fetchone()
    return row


def select_by_task_rows(
    connection: sqlite3.Connection, task_id: str, allowance: HoneyClearance
) -> list[sqlite3.Row]:
    """Select bee_bread entries for `task_id` within `allowance`, oldest first."""
    values = _allowed_clearance_values(allowance)
    placeholders = ",".join("?" for _ in values)
    sql = (
        f"{_SELECT_SQL} WHERE task_id = ? AND clearance IN ({placeholders}) "
        f"AND {UNTAINTED_CLAUSE}{_ORDER_BY}"
    )
    return connection.execute(sql, (task_id, *values)).fetchall()


def select_between_rows(
    connection: sqlite3.Connection, start: datetime, end: datetime, allowance: HoneyClearance
) -> list[sqlite3.Row]:
    """Select bee_bread entries in `[start, end]` within `allowance`, oldest first."""
    values = _allowed_clearance_values(allowance)
    placeholders = ",".join("?" for _ in values)
    sql = (
        f"{_SELECT_SQL} WHERE created_at >= ? AND created_at <= ? "
        f"AND clearance IN ({placeholders}) AND {UNTAINTED_CLAUSE}{_ORDER_BY}"
    )
    return connection.execute(sql, (start.isoformat(), end.isoformat(), *values)).fetchall()
