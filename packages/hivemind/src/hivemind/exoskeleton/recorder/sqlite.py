"""Provide SqliteRecordingStore: flight recordings kept in two tables of the Hive's own database.

The flight recorder (roadmap step 6.6, ADR-0032) keeps evidence of every GUI action taken while an
Exoskeleton (a Cell's optional display, input, audio and browser) is attached: one header per
recording and one row per recorded action, frames included. This is the durable `RecordingStore`:
headers in `exoskeleton_recordings`; each action in `exoskeleton_recording_actions`, its JSON body
beside its before and after frames as PNG BLOB columns (the migration says why columns and not a
frames table). A Frame refuses JSON on purpose, so a write takes the frames out of the action
before dumping it, and a read rebuilds each one with `Frame.from_png` from the stored bytes and
capture time: what comes back is byte for byte what went in. Beyond the protocol, `prune_before`
is the retention sweep the manifest's `[exoskeleton] recording_retention_days` drives.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.recorder`. Opened by a composition root on the Hive's database file
    (`hivemind.cli.stores.open_recordings`); written by `recorder.FlightRecorder`, read by `hive
    recordings` and `recorder.playback`. Calls into `hivemind.common` (sqlite, migrations),
    `hivemind.exoskeleton.errors`, `.frames`, `recorder.models`, `recorder.store` and waggle.

Key invariants:
    - Satisfies `recorder.store.RecordingStore`; the contract suite holds this store and
      `InMemoryRecordingStore` to the same clauses.
    - No PNG byte is ever written into a JSON body: frames live only in the BLOB columns.
    - Every SQLite call runs on the store's `ConnectionThread`, one transaction per write,
      serialised by this instance's own `asyncio.Lock` (codingrules section 11).
    - `purge_cell` and `prune_before` are the only DELETEs, and each removes a recording's actions
      before its header in one transaction, so no action (and no frame) outlives its recording.
    - Every stored timestamp is fixed-width UTC ISO text, so text order is time order.

See Also:
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
    - hivemind.exoskeleton.recorder.migrations for the two tables.
    - hivemind.exoskeleton.recorder.store for the protocol and the in-memory store.
"""

from __future__ import annotations

import asyncio
import importlib.resources
import sqlite3
from datetime import UTC, datetime

from hivemind.common.migrations import apply_migrations, load_migrations
from hivemind.common.sqlite import ConnectionThread, transaction
from hivemind.exoskeleton.errors import RecordingNotFoundError
from hivemind.exoskeleton.frames import Frame
from hivemind.exoskeleton.recorder.models import Evidence, RecordedAction, RecordingInfo
from hivemind.exoskeleton.recorder.store import DEFAULT_LIST_LIMIT
from waggle.clock import Clock

SUBSYSTEM = "exoskeleton_recordings"  # Keys this subsystem's rows in schema_migrations.
# Dotted package path importlib.resources.files() reads the numbered .sql files from; a string,
# not a package import, so this module has no import-time dependency on that package.
MIGRATIONS_PACKAGE = "hivemind.exoskeleton.recorder.migrations"

_INSERT_RECORDING_SQL = (
    "INSERT OR IGNORE INTO exoskeleton_recordings "
    "(id, cell_id, task_id, clearance, started_at, body) VALUES (?, ?, ?, ?, ?, ?)"
)
_SELECT_RECORDING_SQL = "SELECT body FROM exoskeleton_recordings WHERE id = ?"
_NEXT_SEQ_SQL = (
    "SELECT COALESCE(MAX(seq), 0) + 1 FROM exoskeleton_recording_actions WHERE recording_id = ?"
)
_INSERT_ACTION_SQL = (
    "INSERT INTO exoskeleton_recording_actions (recording_id, seq, finished_at, body, "
    "before_png, before_captured_at, after_png, after_captured_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_ACTIONS_SQL = (
    "SELECT body, before_png, before_captured_at, after_png, after_captured_at "
    "FROM exoskeleton_recording_actions WHERE recording_id = ? ORDER BY seq"
)
# Newest first; a tie on started_at falls back to the id so the order is total and repeatable.
_SELECT_LIST_SQL = (
    "SELECT body FROM exoskeleton_recordings ORDER BY started_at DESC, id DESC LIMIT ?"
)
_SELECT_LIST_FOR_CELL_SQL = (
    "SELECT body FROM exoskeleton_recordings WHERE cell_id = ? "
    "ORDER BY started_at DESC, id DESC LIMIT ?"
)
_DELETE_CELL_ACTIONS_SQL = (
    "DELETE FROM exoskeleton_recording_actions WHERE recording_id IN "
    "(SELECT id FROM exoskeleton_recordings WHERE cell_id = ?)"
)
_DELETE_CELL_RECORDINGS_SQL = "DELETE FROM exoskeleton_recordings WHERE cell_id = ?"
# A recording is stale once it started before the cutoff and none of its actions finished at or
# after it, so an attach that is still recording is never pruned out from under its recorder.
# Both statements bind (cutoff, cutoff). Actions go first; the second statement then finds every
# stale header with no action left, while a live one still has the recent action that saved it.
_DELETE_STALE_ACTIONS_SQL = """
DELETE FROM exoskeleton_recording_actions WHERE recording_id IN (
    SELECT r.id FROM exoskeleton_recordings AS r
    WHERE r.started_at < ? AND NOT EXISTS (
        SELECT 1 FROM exoskeleton_recording_actions AS a
        WHERE a.recording_id = r.id AND a.finished_at >= ?
    )
)
"""
_DELETE_STALE_RECORDINGS_SQL = """
DELETE FROM exoskeleton_recordings
WHERE started_at < ? AND NOT EXISTS (
    SELECT 1 FROM exoskeleton_recording_actions AS a
    WHERE a.recording_id = exoskeleton_recordings.id AND a.finished_at >= ?
)
"""

__all__ = [
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "SqliteRecordingStore",
    "apply_recording_migrations",
]


def apply_recording_migrations(connection: sqlite3.Connection, clock: Clock) -> tuple[int, ...]:
    """Apply every pending migration under `hivemind.exoskeleton.recorder.migrations`.

    Synchronous, like every function `hivemind.common.migrations` exports;
    `SqliteRecordingStore.create` runs it under `asyncio.to_thread`.

    Args:
        connection: An open connection from `hivemind.common.sqlite.connect`.
        clock: Stamps each applied migration's `applied_at`.

    Returns:
        The migration versions this call applied, ascending; empty once the schema is current.
    """
    migrations = load_migrations(importlib.resources.files(MIGRATIONS_PACKAGE))
    return apply_migrations(connection, SUBSYSTEM, migrations, clock)


class SqliteRecordingStore:
    """The durable RecordingStore: two SQLite tables, one connection, one lock per instance."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Wrap an already-migrated connection. Prefer `create`, which migrates first.

        Args:
            connection: An open connection whose database already has both recorder tables.
        """
        self._connection = connection
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled await
        # can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-recordings")
        # Serialises every method, matching the other stores' own locks.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(cls, connection: sqlite3.Connection, clock: Clock) -> SqliteRecordingStore:
        """Apply this subsystem's migrations on `connection`, then wrap it.

        The recorder writes no trail event (a recording is evidence, never audit), so unlike the
        memory store this needs no `pheromone_events` table first.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            clock: Stamps the migration records.

        Returns:
            A store whose two tables exist and are current.
        """
        # Blocking: at most one transaction per pending migration (usually zero, once current).
        await asyncio.to_thread(apply_recording_migrations, connection, clock)
        return cls(connection)

    async def open(self, info: RecordingInfo) -> None:
        """Start a recording; opening an id twice keeps the first header. See RecordingStore."""
        async with self._lock:
            # Blocking: one INSERT OR IGNORE; milliseconds.
            await self._thread.run(_open_transaction, self._connection, info)

    async def add(self, recording_id: str, action: RecordedAction) -> None:
        """Append one action after the recording's last; see RecordingStore.add.

        Raises:
            RecordingNotFoundError: No recording has that id.
        """
        async with self._lock:
            # Blocking: an existence check, a MAX(seq) and one INSERT carrying up to two PNGs.
            await self._thread.run(_add_transaction, self._connection, recording_id, action)

    async def info(self, recording_id: str) -> RecordingInfo:
        """Return a recording's header; see RecordingStore.info.

        Raises:
            RecordingNotFoundError: No recording has that id.
        """
        async with self._lock:
            row = await self._thread.run(_select_recording, self._connection, recording_id)
        if row is None:
            raise RecordingNotFoundError(recording_id)
        return RecordingInfo.model_validate_json(row["body"])

    async def actions(self, recording_id: str) -> tuple[RecordedAction, ...]:
        """Return a recording's actions in the order they were added, frames rebuilt.

        Raises:
            RecordingNotFoundError: No recording has that id.
        """
        async with self._lock:
            # Blocking: two indexed SELECTs, then each frame's header read and digest taken on the
            # connection's own thread rather than the event loop.
            return await self._thread.run(_select_actions, self._connection, recording_id)

    async def recordings(
        self, cell_id: str | None = None, limit: int = DEFAULT_LIST_LIMIT
    ) -> tuple[RecordingInfo, ...]:
        """Return recording headers, newest first, optionally one Cell's; see RecordingStore."""
        async with self._lock:
            return await self._thread.run(_select_recordings, self._connection, cell_id, limit)

    async def purge_cell(self, cell_id: str) -> int:
        """Delete every recording of `cell_id`, actions and frames included; return how many."""
        async with self._lock:
            # Blocking: two DELETEs in one transaction, matched on indexed columns.
            return await self._thread.run(_purge_cell_transaction, self._connection, cell_id)

    async def prune_before(self, cutoff: datetime) -> int:
        """Delete every recording with nothing in it at or after `cutoff`: the retention sweep.

        A recording is pruned once it started before `cutoff` and none of its actions finished
        at or after it, so a recording still being written is never removed mid-attach.

        Args:
            cutoff: A timezone-aware moment; typically now minus the retention window.

        Returns:
            How many recordings were removed, their actions and frames with them.

        Raises:
            ValueError: `cutoff` is naive, so it could not be compared with stored UTC times.
        """
        stamp = _utc_text(cutoff)
        async with self._lock:
            # Blocking: two DELETEs in one transaction over the started_at index.
            return await self._thread.run(_prune_transaction, self._connection, stamp)


def _open_transaction(connection: sqlite3.Connection, info: RecordingInfo) -> None:
    """Insert `info`'s header unless its id is already there."""
    with transaction(connection):
        connection.execute(
            _INSERT_RECORDING_SQL,
            (
                info.recording_id,
                info.cell_id,
                info.task_id,
                info.clearance,
                _utc_text(info.started_at),
                info.model_dump_json(),
            ),
        )


def _add_transaction(
    connection: sqlite3.Connection, recording_id: str, action: RecordedAction
) -> None:
    """Insert `action` as the recording's next row, refusing a recording that was never opened."""
    with transaction(connection):
        # An action is never kept without the header it belongs to (the protocol's own rule).
        if connection.execute(_SELECT_RECORDING_SQL, (recording_id,)).fetchone() is None:
            raise RecordingNotFoundError(recording_id)
        # MAX(seq) + 1 inside the same write transaction: BEGIN IMMEDIATE holds the write lock, so
        # no other writer can take the same number in between.
        seq = connection.execute(_NEXT_SEQ_SQL, (recording_id,)).fetchone()[0]
        connection.execute(_INSERT_ACTION_SQL, (recording_id, seq, *_action_columns(action)))


def _select_recording(connection: sqlite3.Connection, recording_id: str) -> sqlite3.Row | None:
    """Return the header row for `recording_id`, or None."""
    row: sqlite3.Row | None = connection.execute(_SELECT_RECORDING_SQL, (recording_id,)).fetchone()
    return row


def _select_actions(
    connection: sqlite3.Connection, recording_id: str
) -> tuple[RecordedAction, ...]:
    """Return the recording's actions, oldest first; raise for a recording never opened."""
    if _select_recording(connection, recording_id) is None:
        raise RecordingNotFoundError(recording_id)
    rows = connection.execute(_SELECT_ACTIONS_SQL, (recording_id,)).fetchall()
    return tuple(_action_from_row(row) for row in rows)


def _select_recordings(
    connection: sqlite3.Connection, cell_id: str | None, limit: int
) -> tuple[RecordingInfo, ...]:
    """Return up to `limit` headers newest first, every Cell's or only `cell_id`'s."""
    if cell_id is None:
        rows = connection.execute(_SELECT_LIST_SQL, (limit,)).fetchall()
    else:
        rows = connection.execute(_SELECT_LIST_FOR_CELL_SQL, (cell_id, limit)).fetchall()
    return tuple(RecordingInfo.model_validate_json(row["body"]) for row in rows)


def _purge_cell_transaction(connection: sqlite3.Connection, cell_id: str) -> int:
    """Delete `cell_id`'s actions, then its headers; return how many headers went."""
    with transaction(connection):
        connection.execute(_DELETE_CELL_ACTIONS_SQL, (cell_id,))
        return connection.execute(_DELETE_CELL_RECORDINGS_SQL, (cell_id,)).rowcount


def _prune_transaction(connection: sqlite3.Connection, cutoff: str) -> int:
    """Delete every stale recording's actions, then the stale headers; return how many headers."""
    with transaction(connection):
        connection.execute(_DELETE_STALE_ACTIONS_SQL, (cutoff, cutoff))
        return connection.execute(_DELETE_STALE_RECORDINGS_SQL, (cutoff, cutoff)).rowcount


def _action_columns(
    action: RecordedAction,
) -> tuple[str, str, bytes | None, str | None, bytes | None, str | None]:
    """Split `action` into its row: finish time, frame-free JSON body, and each side's frame."""
    after = action.after
    frameless = action.model_copy(
        update={
            "before": _without_frame(action.before),
            "after": _without_frame(after) if after is not None else None,
        }
    )
    before_png, before_at = _frame_columns(action.before)
    after_png, after_at = _frame_columns(after)
    return (
        _utc_text(action.finished_at),
        frameless.model_dump_json(),
        before_png,
        before_at,
        after_png,
        after_at,
    )


def _without_frame(evidence: Evidence) -> Evidence:
    """Return `evidence` with its frame taken out, for the JSON body."""
    return evidence.model_copy(update={"frame": None})


def _frame_columns(evidence: Evidence | None) -> tuple[bytes | None, str | None]:
    """Return one side's PNG bytes and capture time, or (None, None) when it has no frame."""
    if evidence is None or evidence.frame is None:
        return None, None
    return evidence.frame.png, _utc_text(evidence.frame.captured_at)


def _action_from_row(row: sqlite3.Row) -> RecordedAction:
    """Rebuild a stored action: its JSON body with each side's frame put back from the BLOBs."""
    frameless = RecordedAction.model_validate_json(row["body"])
    before = _with_frame(frameless.before, row["before_png"], row["before_captured_at"])
    after = frameless.after
    if after is not None:
        after = _with_frame(after, row["after_png"], row["after_captured_at"])
    return frameless.model_copy(update={"before": before, "after": after})


def _with_frame(evidence: Evidence, png: bytes | None, captured_at: str | None) -> Evidence:
    """Return `evidence` with the frame rebuilt from `png`, or unchanged when it had none."""
    if png is None or captured_at is None:
        return evidence
    # from_png re-reads the size from the image and re-takes the digest, so a Frame read back
    # equals the one written, field for field.
    frame = Frame.from_png(bytes(png), datetime.fromisoformat(captured_at))
    return evidence.model_copy(update={"frame": frame})


def _utc_text(moment: datetime) -> str:
    """Render an aware `moment` as fixed-width UTC ISO text, the one timestamp form stored here.

    Raises:
        ValueError: `moment` is naive; its offset cannot be known, so it cannot be compared.
    """
    if moment.tzinfo is None:
        raise ValueError(f"Recording times must be timezone-aware; got {moment.isoformat()}.")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")
