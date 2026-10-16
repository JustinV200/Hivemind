"""Open the durable stores a colonized device's Warden keeps under its state directory.

A Virtual Cell's Warden keeps its memory and its trail in process, because the Cell dies with the
container and the trail is shipped to the Queen before it does. A Warden on a device the Hive
borrows outlives its process: the packet restarts it, a reboot ends it, and what it was doing when
that happened has to be there afterwards, above all the Handoff a paused sub-bee wrote, which a
resumed task is started from. So its memory store, its Pheromone Trail segment and its Leavings
(`persist=True` restore records) are SQLite files under the state directory the packet made for it,
opened the way the Hive's own stores are (the Pheromone Trail's migration first, on every
connection, because the other stores refuse a file without its table).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside `hivemind.cli.in_cell.borrowed`. Called by
    `hivemind.cli.in_cell.borrowed.run`. Calls into `hivemind.cell.leavings`,
    `hivemind.common.sqlite`, `hivemind.memory`, `hivemind.pheromone` and waggle only.

Key invariants:
    - All three stores live in one file, `warden.sqlite`, each over a connection of its own (the
      Hive Stand's arrangement: WAL lets them share the file), so a Handoff written by one run is
      read by the next.
    - The state directory is created owner-only: it holds the Warden's trail, which can name the
      tasks it ran on a machine that is someone's own.
    - The connections are closed by `DeviceStores.close`, once, after the Warden is STOPPED (or
      was never started) and its link closed (`hivemind.cli.in_cell.borrowed.run`'s own last
      step), and never under a Warden that overran its stop. Each store runs its statements on a
      worker thread of its own, and a coroutine cancelled while awaiting one (a sub-bee reaped at
      stop, a dial reaped mid-announcement) leaves the statement running to completion there;
      closing the connection under it from another thread is a crash inside SQLite, not an
      error. So the close first asks each store for one read, which its thread answers only
      after everything handed to it before has finished (`ConnectionThread` runs calls in order),
      and only then closes the connection. A close leaves no WAL writer behind for the packet's
      restart of the process to recover from.

See Also:
    - hivemind.cli.stores for `open_trail`, `open_memory` and `open_leavings`, the Hive's openers.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for why an offline Warden's
      work must survive its restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import HoneyClearance
from hivemind.cell.leavings import SqliteLeavingsStore
from hivemind.common.sqlite import connect
from hivemind.memory import SqliteMemoryStore
from hivemind.pheromone import SqlitePheromoneTrail, TrailQuery
from waggle.clock import Clock

DATABASE_FILE = "warden.sqlite"  # Under the state directory: trail, memory and Leavings.
STATE_DIR_MODE = 0o700  # Owner-only: the trail can name the tasks run on a borrowed machine.

__all__ = ["DATABASE_FILE", "DeviceStores", "open_device_stores"]


@dataclass(frozen=True, slots=True)
class DeviceStores:
    """The three durable stores of one device Warden."""

    trail: SqlitePheromoneTrail  # The Warden's own trail segment, shipped to the Queen.
    memory: SqliteMemoryStore  # Handoffs, notes and Cell Wax; what a restart resumes from.
    leavings: SqliteLeavingsStore  # Restore records a lease persisted instead of undoing.
    connections: tuple[sqlite3.Connection, ...] = ()  # The three stores' own, for `close`.

    async def close(self) -> None:
        """Close the three connections, behind whatever their threads still run; idempotent.

        Call it once the Warden is STOPPED and no coroutine can reach the stores any more (module
        docstring's invariants); a statement a cancelled await left running on a store's thread
        is waited for, not cut under.
        """
        # One read per store, answered only after everything handed to its thread before it has
        # finished: the close that follows then cuts nothing. On a second call the reads find
        # their connection closed (a ProgrammingError, sqlite3's word for it) and nothing is left
        # to wait for.
        with contextlib.suppress(sqlite3.ProgrammingError):
            await self.trail.query(TrailQuery(limit=1))
            await self.memory.list_pins(HoneyClearance.C0)
            await self.leavings.list_all_leavings()
        # Blocking but brief: each close checkpoints the WAL and releases the file's lock.
        await asyncio.to_thread(_close_all, self.connections)


async def open_device_stores(state_dir: Path, clock: Clock) -> DeviceStores:
    """Create `state_dir` if need be and open the three stores in `warden.sqlite` there.

    Args:
        state_dir: The Warden's state directory (`HIVEMIND_STATE_DIR`).
        clock: Stamps migrations and every record the stores write.

    Returns:
        The stores, migrated to the current schema and holding whatever an earlier run left.

    Raises:
        OSError: The directory cannot be created or the database file cannot be opened.
        sqlite3.Error: The file is corrupt or locked past its busy timeout.
    """
    # Blocking but brief: one directory creation, off the event loop like every file operation.
    await asyncio.to_thread(_make_state_dir, state_dir)
    database = state_dir / DATABASE_FILE
    # One connection per store, as the Hive's own openers make them; the trail's migration runs
    # first on each because the memory and Leavings stores refuse a file without its table.
    trail_connection = connect(database)
    trail = await SqlitePheromoneTrail.create(trail_connection, clock)
    memory_connection = connect(database)
    await SqlitePheromoneTrail.create(memory_connection, clock)
    memory = await SqliteMemoryStore.create(memory_connection, clock)
    leavings_connection = connect(database)
    await SqlitePheromoneTrail.create(leavings_connection, clock)
    leavings = await SqliteLeavingsStore.create(leavings_connection, clock)
    return DeviceStores(
        trail=trail,
        memory=memory,
        leavings=leavings,
        connections=(trail_connection, memory_connection, leavings_connection),
    )


def _close_all(connections: tuple[sqlite3.Connection, ...]) -> None:
    """Close every connection; one already closed is left so (sqlite3 makes that a no-op)."""
    for connection in connections:
        connection.close()


def _make_state_dir(state_dir: Path) -> None:
    """Create the state directory (and its parents) owner-only if it is not there yet."""
    state_dir.mkdir(mode=STATE_DIR_MODE, parents=True, exist_ok=True)
