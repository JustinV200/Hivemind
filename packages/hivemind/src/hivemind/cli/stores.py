"""Open the Pheromone Trail and the Brood Chamber for one `hive` CLI invocation.

This is the one place the CLI composes stores until the Hive Manifest lands (phase 3) and can name
a database file and inject a real `ChamberIdentity` on its own. `open_trail` and `open_chamber` are
plain synchronous functions, each running its own `asyncio.run` internally: a typer command body is
sync, so this is the seam where the CLI first steps into the async code every store's `create`
classmethod needs (codingrules section 8.2, the CLI's composition root). `DbOption` is the shared
`--db` typer annotation both `hivemind.cli.tasks` and `hivemind.cli.trail` attach to their own
commands, so the option reads identically everywhere it appears.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Called by `hivemind.cli.tasks` and
    `hivemind.cli.trail`. Calls into `hivemind.brood_chamber`, `hivemind.common.sqlite` and
    `hivemind.pheromone`.

Key invariants:
    - `open_chamber` always applies the Pheromone Trail's migrations on its own connection before
      building the `SqliteTaskStore`, so `SqliteTaskStore.create`'s own loud-failure check (no
      `pheromone_events` table) never fires from a CLI-opened database.
    - `open_trail` and `open_chamber` each open their own `sqlite3.Connection` to `db`; two stores
      that write the same file use separate connections (WAL makes that fine, ADR-0006 decision
      1), so a `hive tasks` and a `hive trail` command run one after another never share state.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md for the "separate connections" decision.
    - hivemind.brood_chamber.chamber for BroodChamber and ChamberIdentity.
    - hivemind.pheromone.sqlite for SqlitePheromoneTrail.create, applied by both functions here.

Public API:
    - DEFAULT_DB: the database file every command falls back to.
    - DbOption: the shared `--db` typer option annotation.
    - open_trail, open_chamber: the two composition functions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, SqliteTaskStore
from hivemind.common.sqlite import connect
from hivemind.pheromone import SqlitePheromoneTrail
from waggle.clock import SystemClock

# A single file in the current directory: good enough before the Hive Manifest (phase 3) names
# one explicitly.
DEFAULT_DB = Path("hive.sqlite3")

# Shared so `hive tasks` and `hive trail` declare `--db` with the exact same flag and help text;
# each command still supplies its own default (DEFAULT_DB) at the parameter, since a typer.Option
# built once at import time cannot see which command it will end up decorating.
DbOption = Annotated[
    Path, typer.Option("--db", help=f"The Hive's SQLite database file (default: {DEFAULT_DB}).")
]

__all__ = ["DEFAULT_DB", "DbOption", "open_chamber", "open_trail"]


def open_trail(db: Path) -> SqlitePheromoneTrail:
    """Open `db` and return a ready SqlitePheromoneTrail, applying its migrations first.

    Args:
        db: The Hive's SQLite database file.

    Returns:
        A SqlitePheromoneTrail whose `pheromone_events` table exists and is current.
    """

    async def _open() -> SqlitePheromoneTrail:
        connection = connect(db)
        return await SqlitePheromoneTrail.create(connection, SystemClock())

    # asyncio.run: a fresh event loop for this one setup call, the seam where a sync typer command
    # body first reaches the async store layer (codingrules section 8.2).
    return asyncio.run(_open())


def open_chamber(db: Path, identity: ChamberIdentity) -> BroodChamber:
    """Open `db` and return a ready BroodChamber, applying both subsystems' migrations first.

    Args:
        db: The Hive's SQLite database file.
        identity: The hive, node and actor every TaskEvent this chamber writes is stamped with.

    Returns:
        A BroodChamber backed by a SqliteTaskStore whose tables exist and are current.
    """

    async def _open() -> BroodChamber:
        connection = connect(db)
        clock = SystemClock()
        # SqliteTaskStore.create refuses to proceed without a pheromone_events table already on
        # its connection, so the trail's own migration runs first, on this same connection, on
        # every call -- a fresh database file is never a problem here.
        await SqlitePheromoneTrail.create(connection, clock)
        store = await SqliteTaskStore.create(connection, clock)
        return BroodChamber(store, clock, identity)

    return asyncio.run(_open())
