"""Provide SqliteLink: one connection, its thread and its lock, shared by every Entrance table.

The Entrance tables (the Hive Entrance's own SQLite tables; the Entrance is the Hive's one HTTP
door) live in one database file and are written through one connection (ADR-0006). The device
tables (``SqliteEntranceStore``) and the tables that login, sessions, step-up and the Entrance
Reducer keep (``hivemind.entrance.store.sessions``, ``.logins``, ``.pending``, ``.mode``) are
separate classes, each one concept, so they share that connection through a ``SqliteLink``: every
blocking body runs on the link's ``ConnectionThread`` (a cancelled await can never leave a
transaction open under the next caller) while holding the link's ``asyncio.Lock`` (a two-hop
read-then-write never interleaves with another table's).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store``. Built by
    ``SqliteEntranceStore`` and handed to each of its tables. Calls into
    ``hivemind.common.sqlite`` only.

Key invariants:
    - Every statement on the connection runs through ``run``, one at a time, in call order.

See Also:
    - hivemind.common.sqlite.ConnectionThread for why one thread per connection.
    - hivemind.entrance.store.sqlite for the store that owns the link.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from hivemind.common.sqlite import ConnectionThread

__all__ = ["SqliteLink"]


@dataclass(frozen=True, slots=True)
class SqliteLink:
    """The one connection every Entrance table writes through, its thread and its lock.

    Attributes:
        connection: An open connection to the Hive's database file, already migrated.
        thread: Runs every blocking call on the connection, in order.
        lock: Held for each call, so a table's read-decide-write is atomic against the others.
    """

    connection: sqlite3.Connection
    thread: ConnectionThread
    lock: asyncio.Lock

    async def run[ResultT](self, body: Callable[..., ResultT], /, *args: object) -> ResultT:
        """Run ``body(connection, *args)`` on the link's thread while holding its lock.

        Args:
            body: A blocking function whose first parameter is the connection.
            *args: Its remaining arguments.

        Returns:
            Whatever ``body`` returns.
        """
        # Latency: one local SQLite statement or transaction; busy_timeout bounds any lock wait.
        async with self.lock:
            return await self.thread.run(body, self.connection, *args)
