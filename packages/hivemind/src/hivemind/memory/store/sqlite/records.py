"""SQL and transactions for the four original memory tables: pins, notes, handoffs, episodes.

Split out of `hivemind.memory.store.sqlite.store` purely by codingrules 5.1's size limit (the
combined pins/notes/handoffs/episodes/bee_bread logic no longer fits one file): this module holds
every constant and private function `hivemind.memory.store.sqlite.store.SqliteMemoryStore`'s
pin/note/handoff/episode methods delegate to, unchanged in behaviour from before the split. Bee
Bread's own table lives in the sibling `hivemind.memory.store.sqlite.bee_bread`, which imports
`_allowed_clearance_values` and `_delete_row` from here rather than duplicating them.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called only by
    `hivemind.memory.store.sqlite.store.SqliteMemoryStore`, on its `ConnectionThread`. Calls into
    hivemind.cell (HoneyClearance), hivemind.common.sqlite (transaction), hivemind.memory
    (episodes, handoff, notes, pins), hivemind.pheromone (insert_event) and sqlite3 only.

Key invariants:
    - Every `_*_transaction` function writes its row and its MemoryEvent inside one
      `hivemind.common.sqlite.transaction` block (codingrules section 12).
    - `_evict_oldest_notes_over_bound` runs inside `_add_note_transaction`'s own transaction, the
      store's own third DELETE alongside `remove_pin`/`remove_note` (see `store.py`).

See Also:
    - hivemind.memory.store.sqlite.store for SqliteMemoryStore, the one caller.
    - hivemind.memory.store.sqlite.bee_bread for the fifth table's own SQL and transactions.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import transaction
from hivemind.memory.episodes import EpisodeRecord
from hivemind.memory.handoff import Handoff
from hivemind.memory.notes import MAX_NOTES_PER_AUTHOR, Note
from hivemind.memory.pins import Pin
from hivemind.memory.taint import require_unlabelled
from hivemind.pheromone import MemoryEvent, insert_event

# No __all__: every name here is an internal collaborator of hivemind.memory.store.sqlite.store,
# imported by that sibling module only (same subsystem, not a package-boundary crossing that
# codingrules 5.4's import-linter contract would flag); the package's own face
# (hivemind.memory.store.sqlite.__init__) re-exports nothing from this module.

_INSERT_PIN_SQL = (
    "INSERT INTO memory_pins (id, clearance, source, created_at, body) VALUES (?, ?, ?, ?, ?)"
)
_DELETE_PIN_SQL = "DELETE FROM memory_pins WHERE id = ?"
_SELECT_PINS_SQL = "SELECT body FROM memory_pins"
_ORDER_PINS_BY = " ORDER BY created_at, id"

_INSERT_NOTE_SQL = (
    "INSERT INTO memory_notes (id, author, clearance, written_at, body) VALUES (?, ?, ?, ?, ?)"
)
_DELETE_NOTE_SQL = "DELETE FROM memory_notes WHERE id = ?"
_SELECT_NOTES_SQL = "SELECT body FROM memory_notes"
_SELECT_AUTHOR_NOTE_IDS_SQL = "SELECT id FROM memory_notes WHERE author = ? ORDER BY written_at, id"
_ORDER_NOTES_BY = " ORDER BY written_at, id"

_INSERT_HANDOFF_SQL = (
    "INSERT INTO memory_handoffs (event_id, task_id, clearance, written_at, body) "
    "VALUES (?, ?, ?, ?, ?)"
)
_SELECT_HANDOFF_SQL = "SELECT clearance, body FROM memory_handoffs WHERE event_id = ?"

_INSERT_EPISODE_SQL = (
    "INSERT INTO memory_episodes (id, principal, at, clearance, body) VALUES (?, ?, ?, ?, ?)"
)
_SELECT_EPISODES_SQL = "SELECT body FROM memory_episodes"
_DELETE_EPISODES_BEFORE_SQL = "DELETE FROM memory_episodes WHERE at < ?"
_ORDER_EPISODES_BY_DESC = " ORDER BY at DESC, id DESC"
# Roadmap 10.6d: a read that could feed a prompt never returns a tainted row (migration 0004).
UNTAINTED_CLAUSE = "taint_state IS NOT 'tainted'"


def _allowed_clearance_values(allowance: HoneyClearance) -> tuple[str, ...]:
    """Return every HoneyClearance wire value at or below `allowance`'s rank."""
    return tuple(level.value for level in HoneyClearance if level.rank <= allowance.rank)


def _delete_row(connection: sqlite3.Connection, sql: str, row_id: str) -> None:
    """Run one parametrised DELETE by id, inside its own transaction."""
    with transaction(connection):
        connection.execute(sql, (row_id,))


def add_pin_transaction(connection: sqlite3.Connection, pin: Pin, event: MemoryEvent) -> None:
    """Insert one pin row then its event, in one transaction; run on the store's thread."""
    with transaction(connection):
        connection.execute(
            _INSERT_PIN_SQL,
            (
                pin.id,
                pin.clearance.value,
                pin.source.value,
                pin.created_at.isoformat(),
                pin.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def select_pins_rows(
    connection: sqlite3.Connection, allowance: HoneyClearance
) -> list[sqlite3.Row]:
    """Select every pin within `allowance`, in list_pins' documented order."""
    values = _allowed_clearance_values(allowance)
    placeholders = ",".join("?" for _ in values)
    sql = f"{_SELECT_PINS_SQL} WHERE clearance IN ({placeholders}){_ORDER_PINS_BY}"
    return connection.execute(sql, values).fetchall()


def delete_pin(connection: sqlite3.Connection, pin_id: str) -> None:
    """Delete the pin with id `pin_id`, inside its own transaction."""
    _delete_row(connection, _DELETE_PIN_SQL, pin_id)


def add_note_transaction(connection: sqlite3.Connection, note: Note, event: MemoryEvent) -> None:
    """Insert one note row, its event, then evict its author's oldest past the bound."""
    with transaction(connection):
        connection.execute(
            _INSERT_NOTE_SQL,
            (
                note.id,
                note.author,
                note.clearance.value,
                note.written_at.isoformat(),
                note.model_dump_json(),
            ),
        )
        insert_event(connection, event)
        _evict_oldest_notes_over_bound(connection, note.author)


def _evict_oldest_notes_over_bound(connection: sqlite3.Connection, author: str) -> None:
    """Delete `author`'s oldest notes past MAX_NOTES_PER_AUTHOR, inside the caller's transaction.

    The store's third DELETE (with `remove_pin` and `purge_episodes_before`): the per-author bound
    is enforced here, as part of `add_note_transaction`'s own transaction, rather than as a
    separate `MemoryStore` method, because nothing above this store ever needs to trigger an
    eviction on its own (codingrules 8.9: notes are "bounded", and the store is what bounds them).
    """
    rows = connection.execute(_SELECT_AUTHOR_NOTE_IDS_SQL, (author,)).fetchall()
    ids = [row["id"] for row in rows]
    # Oldest first (the SELECT's own order): drop from the front until back within the bound.
    excess = len(ids) - MAX_NOTES_PER_AUTHOR
    for stale_id in ids[: max(excess, 0)]:
        connection.execute(_DELETE_NOTE_SQL, (stale_id,))


def select_notes_rows(
    connection: sqlite3.Connection, author: str | None, allowance: HoneyClearance, limit: int
) -> list[sqlite3.Row]:
    """Select notes within `allowance`, optionally filtered by `author`, in list_notes' order."""
    values = _allowed_clearance_values(allowance)
    clauses = [f"clearance IN ({','.join('?' for _ in values)})"]
    params: list[object] = list(values)
    if author is not None:
        clauses.append("author = ?")
        params.append(author)
    sql = f"{_SELECT_NOTES_SQL} WHERE {' AND '.join(clauses)}{_ORDER_NOTES_BY} LIMIT ?"
    params.append(limit)
    return connection.execute(sql, params).fetchall()


def delete_note(connection: sqlite3.Connection, note_id: str) -> None:
    """Delete the note with id `note_id`, inside its own transaction."""
    _delete_row(connection, _DELETE_NOTE_SQL, note_id)


def put_handoff_transaction(
    connection: sqlite3.Connection,
    event_id: str,
    handoff: Handoff,
    task_id: str | None,
    event: MemoryEvent,
) -> None:
    """Insert one handoff row then its event, in one transaction; run on the store's thread."""
    require_unlabelled(handoff.tainted, event_id)  # Only the taint ledger writes a label.
    with transaction(connection):
        connection.execute(
            _INSERT_HANDOFF_SQL,
            (
                event_id,
                task_id,
                handoff.clearance.value,
                event.at.isoformat(),
                handoff.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def select_handoff_row(connection: sqlite3.Connection, event_id: str) -> sqlite3.Row | None:
    """Select one handoff's clearance and body row by event id, or None when no row matches."""
    row: sqlite3.Row | None = connection.execute(_SELECT_HANDOFF_SQL, (event_id,)).fetchone()
    return row


def put_episode_transaction(
    connection: sqlite3.Connection, record: EpisodeRecord, event: MemoryEvent
) -> None:
    """Insert one episode row then its event, in one transaction; run on the store's thread."""
    require_unlabelled(record.tainted, record.id)  # Only the taint ledger writes a label.
    with transaction(connection):
        connection.execute(
            _INSERT_EPISODE_SQL,
            (
                record.id,
                record.principal,
                record.at.isoformat(),
                record.clearance.value,
                record.model_dump_json(),
            ),
        )
        insert_event(connection, event)


def select_episodes_rows(
    connection: sqlite3.Connection, principal: str | None, allowance: HoneyClearance, limit: int
) -> list[sqlite3.Row]:
    """Select untainted episodes within `allowance`, optionally by `principal`, newest first."""
    values = _allowed_clearance_values(allowance)
    clauses = [f"clearance IN ({','.join('?' for _ in values)})", UNTAINTED_CLAUSE]
    params: list[object] = list(values)
    if principal is not None:
        clauses.append("principal = ?")
        params.append(principal)
    sql = f"{_SELECT_EPISODES_SQL} WHERE {' AND '.join(clauses)}{_ORDER_EPISODES_BY_DESC} LIMIT ?"
    params.append(limit)
    return connection.execute(sql, params).fetchall()


def purge_episodes_transaction(connection: sqlite3.Connection, cutoff: datetime) -> int:
    """Delete episodes recorded before `cutoff`, in one transaction; return how many."""
    with transaction(connection):
        cursor = connection.execute(_DELETE_EPISODES_BEFORE_SQL, (cutoff.isoformat(),))
        return cursor.rowcount
