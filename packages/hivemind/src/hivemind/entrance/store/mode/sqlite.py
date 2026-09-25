"""Provide SqliteModeTable: the Entrance's mode in the Entrance tables, written with its event.

The durable ``ModeTable``, over ``entrance_mode`` (migration 0002: one row, and a CHECK that makes a
second impossible) in the Hive's own database file, through the connection every Entrance table
shares (``SqliteLink``). A change is one ``BEGIN IMMEDIATE`` transaction that re-reads the row,
refuses a stale expectation, writes the new mode and inserts its ``guard.*`` event
(``hivemind.pheromone.insert_event``) on the same connection, so the mode and the event commit
together or not at all, and a restart reads back exactly the mode the last committed change left.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.mode``. Built by
    ``SqliteEntranceStore`` over its link. Calls into ``hivemind.common.sqlite``,
    ``hivemind.pheromone.insert_event`` and the protocol's rule.

Key invariants:
    - No row means OPEN; the row is written by the first change.

See Also:
    - hivemind.entrance.store.mode.protocol for ModeTable.
    - hivemind.entrance.store.migrations for the 0002 tables.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from hivemind.common.sqlite import transaction
from hivemind.entrance.errors import EntranceModeConflictError
from hivemind.entrance.reducer import EntranceMode
from hivemind.entrance.store.link import SqliteLink
from hivemind.entrance.store.mode.protocol import check_mode_change
from hivemind.pheromone import GuardEvent, insert_event

_SELECT_SQL = "SELECT mode FROM entrance_mode WHERE id = 1"
_UPSERT_SQL = """
INSERT INTO entrance_mode (id, mode, changed_at) VALUES (1, ?, ?)
ON CONFLICT (id) DO UPDATE SET mode = excluded.mode, changed_at = excluded.changed_at
"""

__all__ = ["SqliteModeTable"]


class SqliteModeTable:
    """The durable ModeTable: one row, written through the Entrance's one connection."""

    def __init__(self, link: SqliteLink) -> None:
        """Wrap the shared link; ``SqliteEntranceStore`` builds this once.

        Args:
            link: The Entrance tables' connection, thread and lock.
        """
        self._link = link

    async def get(self) -> EntranceMode:
        """Return the mode; see ModeTable.get."""
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        return await self._link.run(_read)

    async def change(self, expected: EntranceMode, new: EntranceMode, event: GuardEvent) -> None:
        """Change the mode with its event; see ModeTable.change."""
        check_mode_change(expected, new, event)
        # Blocking, sub-millisecond: one local statement or transaction on the link's thread.
        await self._link.run(_change, (expected, new), event.at, event)


# ──────────────────────────────────────────────────────────────────────────────
# Blocking bodies: each runs on the link's connection thread, never on the event loop.
# ──────────────────────────────────────────────────────────────────────────────


def _read(connection: sqlite3.Connection) -> EntranceMode:
    """Read the persisted mode; OPEN when no change was ever written."""
    row = connection.execute(_SELECT_SQL).fetchone()
    return EntranceMode(row["mode"]) if row is not None else EntranceMode.OPEN


def _change(
    connection: sqlite3.Connection,
    edge: tuple[EntranceMode, EntranceMode],
    at: datetime,
    event: GuardEvent,
) -> None:
    """Re-read the mode, refuse a stale expectation, write the new one and its event."""
    expected, new = edge
    with transaction(connection):
        current = _read(connection)
        if current is not expected:
            raise EntranceModeConflictError(expected, current)
        connection.execute(_UPSERT_SQL, (new.value, at.isoformat()))
        insert_event(connection, event)
