"""Keep what a restarted Queen needs of a living Night Veil Cell: counts and ids, never records.

A Night Veil Cell's segment lives in the Queen's memory alone (`segments`), which is what the
boundary wants: a Queen that stops loses the Cell's records, as a purge would. Two things the
purge still owes the Cell would go with them, though. Its per-tier Capping counts, which the
skeleton keeps as `capping.summary` whoever ends the Cell (a restarted Queen's sweep, an offline
Absconding), and the ids that belong to it (its tasks, its Wardens, its grants), which the purge's
side channels need to find the Cell's rows in the stores beside the trail. So `EphemeralSegments`
checkpoints, per living Night Veil Cell, exactly those: the counts per tier, the moment the newest
counted Capping event happened with the ids of the events counted at that very moment (so an event
its Warden ships again after the restart is not counted twice), and the member ids. Numbers and ids
only: no payload, kind or text of any record is ever written here. The purge forgets a Cell's
checkpoint when it takes the Cell, and a restarted Queen recalls a checkpoint when it holds the
Cell again or purges it.

Fits into the Hive:
    Layer 1 (foundational services; capacity as data), inside `hivemind.pheromone.retention`.
    Written and read by `hivemind.pheromone.retention.segments.EphemeralSegments` through
    `Checkpointer`; built by the composition root (`hivemind.cli.compose.night_veil`). Calls into
    `hivemind.common.migrations`, `hivemind.common.sqlite`, `hivemind.pheromone.events`,
    `hivemind.pheromone.retention.skeleton` (TierCount, merge_counts, tier_counts) and waggle
    only.

Key invariants:
    - A checkpoint holds counts, one timestamp and ids, never an event or a word of one.
    - `forget` is idempotent, and the table's only DELETE; a Cell purged leaves no row.
    - The SQLite store opens its own connection to the Hive's file on first use, applying its own
      migration series (`SUBSYSTEM`) first.

See Also:
    - .claude/codingrules.md section 12 for the boundary, and what the skeleton keeps.
    - hivemind.pheromone.retention.segments for when a checkpoint is written, recalled, forgotten.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, connect, transaction
from hivemind.pheromone.events import PheromoneEvent
from hivemind.pheromone.retention.skeleton import TierCount, merge_counts, tier_counts
from waggle.clock import Clock
from waggle.ids import CellId

SUBSYSTEM = "night_veil_checkpoints"  # Keys this table's migrations in schema_migrations.
MIGRATIONS_PACKAGE = "hivemind.pheromone.retention"  # Its .sql files sit beside these modules.
_UPSERT_SQL = (
    "INSERT INTO night_veil_checkpoints (cell_id, body) VALUES (?, ?) "
    "ON CONFLICT (cell_id) DO UPDATE SET body = excluded.body"
)
_SELECT_SQL = "SELECT body FROM night_veil_checkpoints WHERE cell_id = ?"
_DELETE_SQL = "DELETE FROM night_veil_checkpoints WHERE cell_id = ?"
_CAPPING_FAMILY = "capping"  # The only events a tally counts.

__all__ = [
    "CellCheckpoint",
    "Checkpointer",
    "LazySqliteCheckpoints",
    "MemoryCheckpoints",
    "NightVeilCheckpoints",
    "TierTally",
]


class TierTally(BaseModel):
    """One risk tier's counts in a checkpoint: `TierCount`, as a stored value."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tier: str
    approved: int
    rejected: int
    rolled_back: int

    @classmethod
    def of(cls, count: TierCount) -> TierTally:
        """Return `count` as a stored tally."""
        return cls(
            tier=count.tier,
            approved=count.approved,
            rejected=count.rejected,
            rolled_back=count.rolled_back,
        )

    def count(self) -> TierCount:
        """Return this tally as the `TierCount` the purge summarises."""
        return TierCount(self.tier, self.approved, self.rejected, self.rolled_back)


class CellCheckpoint(BaseModel):
    """What a restarted Queen needs of one living Night Veil Cell: counts, a moment, its ids.

    Attributes:
        counts: Its Capping outcomes so far, per risk tier.
        counted_through: When the newest Capping event counted in `counts` happened; None before
            the first. An event before it is never counted again.
        counted_at: The ids of the counted events at `counted_through` itself: one of those
            shipped again is not counted again, while a new event at the same instant is.
        members: Every id filed under the Cell: its tasks, Wardens, nodes, grants and the rest.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    counts: tuple[TierTally, ...] = ()
    counted_through: datetime | None = None
    counted_at: frozenset[str] = frozenset()
    members: frozenset[str] = frozenset()

    def counted(self, event: PheromoneEvent) -> bool:
        """Return whether `event` is already in `counts`, by its moment and, at the edge, its id."""
        through = self.counted_through
        if through is None or event.at > through:
            return False
        return event.at < through or event.id in self.counted_at


class NightVeilCheckpoints(Protocol):
    """Store one checkpoint per living Night Veil Cell, until the purge forgets it."""

    async def save(self, cell_id: CellId, checkpoint: CellCheckpoint) -> None:
        """Replace `cell_id`'s checkpoint with `checkpoint`."""
        ...

    async def recall(self, cell_id: CellId) -> CellCheckpoint | None:
        """Return `cell_id`'s checkpoint, or None when none was saved (or it was forgotten)."""
        ...

    async def forget(self, cell_id: CellId) -> None:
        """Remove `cell_id`'s checkpoint; idempotent."""
        ...


class Checkpointer:
    """Count each held Night Veil Cell's Capping outcomes and checkpoint them with its ids.

    `EphemeralSegments` tells it what changed (`touch`, `count`) and when to write (`save`, after
    each record it keeps or segment it merges); a restarted Queen's first read of a Cell
    (`recall`) brings back what an earlier Queen counted, and `taken` returns the Cell's whole
    tally and forgets its checkpoint. Owns its state in place (codingrules 8.5): per Cell, the
    earlier Queen's checkpoint, the Capping events counted since, and whether anything changed.
    """

    def __init__(self, store: NightVeilCheckpoints | None) -> None:
        """Checkpoint into `store`; None keeps nothing (a boundary with no durable side).

        Args:
            store: Where each held Cell's checkpoint is saved, recalled and forgotten.
        """
        self._store = store
        self._recalled: dict[CellId, CellCheckpoint] = {}
        # By event id: a segment shipped twice (a retry) is merged once, and counted once.
        self._events: dict[CellId, dict[str, PheromoneEvent]] = {}
        self._dirty: set[CellId] = set()

    def touch(self, cell_id: CellId) -> None:
        """Note that `cell_id`'s ids changed, so its next `save` writes."""
        self._dirty.add(cell_id)

    def count(self, cell_id: CellId, events: Iterable[PheromoneEvent]) -> None:
        """Count every Capping event among `events` (the rest are not this tally's to keep)."""
        counted = self._events.setdefault(cell_id, {})
        for event in events:
            if event.family == _CAPPING_FAMILY and event.id not in counted:
                counted[event.id] = event
                self._dirty.add(cell_id)

    async def save(self, cell_id: CellId, members: frozenset[str]) -> None:
        """Write `cell_id`'s checkpoint when something changed since the last write."""
        if self._store is None or cell_id not in self._dirty:
            return
        self._dirty.discard(cell_id)
        await self._store.save(cell_id, self._tally(cell_id, members))

    async def recall(self, cell_id: CellId) -> frozenset[str]:
        """Bring back what an earlier Queen checkpointed of `cell_id`; return its member ids."""
        if self._store is None or cell_id in self._recalled:
            return frozenset()
        recalled = await self._store.recall(cell_id)
        self._recalled[cell_id] = recalled if recalled is not None else CellCheckpoint()
        return self._recalled[cell_id].members

    async def taken(self, cell_id: CellId) -> tuple[TierCount, ...]:
        """Return `cell_id`'s whole per-tier tally, and forget its checkpoint (the purge's)."""
        await self.recall(cell_id)  # A Cell this Queen never held: an earlier one may have.
        tally = self._tally(cell_id, frozenset())
        self._recalled.pop(cell_id, None)
        self._events.pop(cell_id, None)
        self._dirty.discard(cell_id)
        if self._store is not None:
            await self._store.forget(cell_id)
        return tuple(count.count() for count in tally.counts)

    def _tally(self, cell_id: CellId, members: frozenset[str]) -> CellCheckpoint:
        """Merge the earlier Queen's counts with this one's, counting nothing twice."""
        earlier = self._recalled.get(cell_id, CellCheckpoint())
        # What the earlier Queen counted, its Warden may ship again (the event at its cursor);
        # only the rest are this Queen's to add.
        held = self._events.get(cell_id, {}).values()
        fresh = [event for event in held if not earlier.counted(event)]
        counts = merge_counts((c.count() for c in earlier.counts), tier_counts(fresh))
        newest = max((event.at for event in fresh), default=earlier.counted_through)
        edge = {event.id for event in fresh if event.at == newest}
        if newest == earlier.counted_through:
            edge |= earlier.counted_at  # The earlier edge still stands beside this one's.
        return CellCheckpoint(
            counts=tuple(TierTally.of(count) for count in counts),
            counted_through=newest,
            counted_at=frozenset(edge),
            members=earlier.members | members,
        )


class MemoryCheckpoints:
    """In-process checkpoints, for a Hive over an in-memory trail (tests and demos)."""

    def __init__(self) -> None:
        """Build a store holding no checkpoint yet."""
        self._rows: dict[CellId, CellCheckpoint] = {}

    async def save(self, cell_id: CellId, checkpoint: CellCheckpoint) -> None:
        """Replace `cell_id`'s checkpoint; see `NightVeilCheckpoints.save`."""
        self._rows[cell_id] = checkpoint

    async def recall(self, cell_id: CellId) -> CellCheckpoint | None:
        """Return `cell_id`'s checkpoint; see `NightVeilCheckpoints.recall`."""
        return self._rows.get(cell_id)

    async def forget(self, cell_id: CellId) -> None:
        """Remove `cell_id`'s checkpoint; see `NightVeilCheckpoints.forget`."""
        self._rows.pop(cell_id, None)

    def rows(self) -> Mapping[CellId, CellCheckpoint]:
        """Every checkpoint held now, for a test to assert against."""
        return dict(self._rows)


@dataclass(slots=True)
class _Opened:
    """The one connection and thread a `LazySqliteCheckpoints` opened, once."""

    connection: sqlite3.Connection
    thread: ConnectionThread


class LazySqliteCheckpoints:
    """Checkpoints in the Hive's own SQLite file, over a connection opened at first use.

    Most processes that build the boundary never checkpoint (an offline command that ends no
    Night Veil Cell, a Hive that runs none), so none opens a connection it never uses.
    """

    def __init__(self, database: Path, clock: Clock) -> None:
        """Remember the Hive's file; nothing is opened yet.

        Args:
            database: The Hive's SQLite file (`[hive] db`).
            clock: Stamps the migration record, the first time the table is created.
        """
        self._database = database
        self._clock = clock
        self._opened: _Opened | None = None
        # One opening at a time, and every statement after it in order.
        self._lock = asyncio.Lock()

    async def save(self, cell_id: CellId, checkpoint: CellCheckpoint) -> None:
        """Replace `cell_id`'s checkpoint; see `NightVeilCheckpoints.save`."""
        await self._run(_write, _UPSERT_SQL, (cell_id, checkpoint.model_dump_json()))

    async def recall(self, cell_id: CellId) -> CellCheckpoint | None:
        """Return `cell_id`'s checkpoint; see `NightVeilCheckpoints.recall`."""
        body = await self._run(_read, _SELECT_SQL, (cell_id,))
        return None if body is None else CellCheckpoint.model_validate_json(body)

    async def forget(self, cell_id: CellId) -> None:
        """Remove `cell_id`'s checkpoint; see `NightVeilCheckpoints.forget`."""
        await self._run(_write, _DELETE_SQL, (cell_id,))

    async def _run[Result](
        self, call: Callable[..., Result], sql: str, parameters: tuple[str, ...]
    ) -> Result:
        """Open the connection on first use, then run `call` on its own thread, in order."""
        async with self._lock:
            if self._opened is None:
                # Blocking: opening the file and applying this table's one migration, once.
                connection = await asyncio.to_thread(_open, self._database, self._clock)
                self._opened = _Opened(connection, ConnectionThread("hive-night-veil"))
            opened = self._opened
            return await opened.thread.run(call, opened.connection, sql, parameters)


def _open(database: Path, clock: Clock) -> sqlite3.Connection:
    """Open the Hive's file and create the checkpoint table if this is its first use."""
    connection = connect(database)
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    apply_migrations(connection, SUBSYSTEM, migrations, clock)
    return connection


def _write(connection: sqlite3.Connection, sql: str, parameters: tuple[str, ...]) -> None:
    """Run one upsert or delete in its own transaction."""
    with transaction(connection):
        connection.execute(sql, parameters)


def _read(connection: sqlite3.Connection, sql: str, parameters: tuple[str, ...]) -> str | None:
    """Return the one body `sql` selects, or None."""
    row = connection.execute(sql, parameters).fetchone()
    return None if row is None else str(row["body"])
