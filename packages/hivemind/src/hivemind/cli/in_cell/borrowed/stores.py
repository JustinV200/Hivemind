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
    - The connections are never closed explicitly. Each store runs its statements on a worker
      thread of its own, and a coroutine cancelled while awaiting one (a sub-bee reaped at stop)
      leaves the statement running to completion there; closing the connection under it from
      another thread is a crash inside SQLite, not an error. The process ends right after the
      Warden stops, which joins those threads first, and WAL recovery covers the files.

See Also:
    - hivemind.cli.stores for `open_trail`, `open_memory` and `open_leavings`, the Hive's openers.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for why an offline Warden's
      work must survive its restart.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell.leavings import SqliteLeavingsStore
from hivemind.common.sqlite import connect
from hivemind.memory import SqliteMemoryStore
from hivemind.pheromone import SqlitePheromoneTrail
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
    trail = await SqlitePheromoneTrail.create(connect(database), clock)
    memory_connection = connect(database)
    await SqlitePheromoneTrail.create(memory_connection, clock)
    memory = await SqliteMemoryStore.create(memory_connection, clock)
    leavings_connection = connect(database)
    await SqlitePheromoneTrail.create(leavings_connection, clock)
    leavings = await SqliteLeavingsStore.create(leavings_connection, clock)
    return DeviceStores(trail=trail, memory=memory, leavings=leavings)


def _make_state_dir(state_dir: Path) -> None:
    """Create the state directory (and its parents) owner-only if it is not there yet."""
    state_dir.mkdir(mode=STATE_DIR_MODE, parents=True, exist_ok=True)
