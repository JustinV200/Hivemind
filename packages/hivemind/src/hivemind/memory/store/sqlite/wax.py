"""SQL and transactions for the sixth memory table: memory_cell_wax (roadmap step 4.2a).

Split from `hivemind.memory.store.sqlite.records` (the original four tables) and
`hivemind.memory.store.sqlite.bee_bread` (the fifth) purely by codingrules 5.1's size limit; this
module's shape mirrors those two exactly, one table's worth. Reuses
`records._allowed_clearance_values`, the one piece of shared logic between all three, rather than
duplicating it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called only by
    `hivemind.memory.store.sqlite.store.SqliteMemoryStore`, under `asyncio.to_thread`. Calls into
    hivemind.cell (HoneyClearance), hivemind.common.sqlite (transaction), hivemind.memory.cell_wax
    (CellWax, WaxState), hivemind.pheromone (insert_event), hivemind.memory.store.sqlite.records
    and sqlite3 only.

Key invariants:
    - `put_wax_transaction` writes its row and its MemoryEvent inside one
      `hivemind.common.sqlite.transaction` block (codingrules section 12); so does
      `update_wax_state_transaction`.
    - `select_wax_rows` never filters by state on its own: the caller always passes the exact set
      of `WaxState` wire values it wants, mirroring `MemoryStore.list_wax`'s own contract.

See Also:
    - hivemind.memory.store.sqlite.store for SqliteMemoryStore, the one caller.
    - hivemind.memory.store.sqlite.records for `_allowed_clearance_values`, reused here.
    - hivemind.memory.cell_wax for CellWax and WaxState, the shapes this module persists.
"""

from __future__ import annotations

import sqlite3

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import transaction
from hivemind.memory.cell_wax import CellWax, WaxState
from hivemind.memory.store.sqlite.records import _allowed_clearance_values
from hivemind.pheromone import MemoryEvent, insert_event

# No __all__: see hivemind.memory.store.sqlite.records's own note; the same reasoning applies here.

_INSERT_SQL = (
    "INSERT INTO memory_cell_wax "
    "(id, cell_id, state, severity, clearance, expires_at, proposed_at, body) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_UPDATE_SQL = (
    "UPDATE memory_cell_wax SET state = ?, severity = ?, clearance = ?, body = ? WHERE id = ?"
)
_SELECT_BY_ID_SQL = "SELECT body FROM memory_cell_wax WHERE id = ?"
_SELECT_SQL = "SELECT body FROM memory_cell_wax"
_ORDER_BY_NEWEST = " ORDER BY proposed_at DESC, id DESC"


def put_wax_transaction(connection: sqlite3.Connection, wax: CellWax, event: MemoryEvent) -> None:
    """Insert one wax row then its event, in one transaction; run under to_thread."""
    with transaction(connection):
        connection.execute(
            _INSERT_SQL,
            (
                wax.id,
                wax.cell_id,
                wax.state.value,
                wax.severity.value,
                wax.clearance.value,
                wax.expires_at.isoformat() if wax.expires_at is not None else None,
                wax.proposed_at.isoformat(),
                wax.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def select_by_id_row(connection: sqlite3.Connection, wax_id: str) -> sqlite3.Row | None:
    """Select one wax note's body row by id, or None when no row matches."""
    row: sqlite3.Row | None = connection.execute(_SELECT_BY_ID_SQL, (wax_id,)).fetchone()
    return row


def select_wax_rows(
    connection: sqlite3.Connection,
    cell_id: str | None,
    states: frozenset[WaxState],
    allowance: HoneyClearance,
) -> list[sqlite3.Row]:
    """Select wax notes in `states` within `allowance`, newest first.

    Args:
        connection: An open connection.
        cell_id: Only notes about this Cell; `None` selects every Cell's.
        states: Only notes currently in one of these states.
        allowance: The reader's clearance ceiling.
    """
    clearance_values = _allowed_clearance_values(allowance)
    state_values = tuple(state.value for state in states)
    clauses = [
        f"clearance IN ({','.join('?' for _ in clearance_values)})",
        f"state IN ({','.join('?' for _ in state_values)})",
    ]
    params: list[object] = [*clearance_values, *state_values]
    if cell_id is not None:
        clauses.append("cell_id = ?")
        params.append(cell_id)
    sql = f"{_SELECT_SQL} WHERE {' AND '.join(clauses)}{_ORDER_BY_NEWEST}"
    return connection.execute(sql, params).fetchall()


def update_wax_state_transaction(
    connection: sqlite3.Connection, wax: CellWax, event: MemoryEvent
) -> None:
    """Update one wax row to its next state then insert its event, in one transaction."""
    with transaction(connection):
        connection.execute(
            _UPDATE_SQL,
            (
                wax.state.value,
                wax.severity.value,
                wax.clearance.value,
                wax.model_dump_json(),
                wax.id,
            ),
        )
        insert_event(connection, event)
