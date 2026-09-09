"""Open a SQLite connection with the Hive's standard pragmas, and run one explicit transaction.

Every SQLite-backed subsystem (the Pheromone Trail, the Brood Chamber, and every later store)
opens its connection through :func:`connect` and wraps each ordinary write (a plain ``execute``,
not ``executescript``) in :func:`transaction`, so the pragmas and the locking discipline are set
in exactly one place instead of once per subsystem. SQLite itself is a blocking C library with no
async API; codingrules section 11 requires that kind of call to run under ``asyncio.to_thread`` in
the adapter that owns it, never in this module or in core decision logic, so everything here is
deliberately synchronous.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Called by every subsystem's own
    ``sqlite.py`` adapter (starting with ``hivemind.pheromone.trail.sqlite``), always from inside
    ``asyncio.to_thread``. ``hivemind.common.migrations`` uses :func:`connect`'s connections but
    not :func:`transaction` itself, for the reason documented on that function.

Key invariants:
    - A connection returned by ``connect`` is in autocommit mode (``isolation_level=None``): no
      write happens outside an explicit ``transaction`` block.
    - ``transaction`` never leaves a connection mid-write: it commits on success, rolls back and
      re-raises on any exception, and refuses to nest on a connection that is already inside one.
    - ``transaction`` must never wrap a call to ``connection.executescript(...)``.
      ``executescript`` unconditionally commits whatever transaction is already pending the
      moment it is called -- a hard-coded behaviour of the sqlite3 C extension, independent of
      ``isolation_level`` -- so a script run after this function's own ``BEGIN IMMEDIATE`` would
      be committed piecemeal, statement by statement, instead of atomically, and the ``COMMIT``
      this function issues on a clean exit would then fail with "cannot commit - no transaction
      is active" because nothing would still be pending. ``hivemind.common.migrations`` needs
      exactly that call and works around it by making the migration script's own text open the
      transaction as its first statement instead of issuing a prior ``BEGIN`` through this
      function, so nothing is pending when ``executescript`` runs and the implicit-commit rule
      never fires; see that module's ``_apply_one`` for the verified-safe pattern in full.

See Also:
    - docs/adr/0006-sqlite-through-the-standard-library.md for why plain ``sqlite3`` was chosen
      over ``aiosqlite``.
    - hivemind.common.migrations for the schema-versioning layer built on top of this module.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# 5 seconds is long enough for a WAL checkpoint to finish under normal write load (the Pheromone
# Trail and the Brood Chamber both write in short bursts), but short enough that a genuine
# deadlock (two connections each holding a lock the other wants) surfaces as an error within a
# human-noticeable time instead of hanging the calling thread indefinitely.
BUSY_TIMEOUT_MS = 5_000

# The literal SQLite uses for "no file, in-process only"; WAL journalling needs a real file to put
# the -wal and -shm siblings next to, so an in-memory database is the one case connect() skips it.
_MEMORY_PATH = ":memory:"

__all__ = ["BUSY_TIMEOUT_MS", "connect", "transaction"]


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection configured the way every Hive store expects.

    Args:
        path: The database file to open, or ``":memory:"`` for a private in-memory database
            (used by tests and by :mod:`hivemind.pheromone.trail.memory`-style fakes that still
            want a real SQLite engine without a file on disk).

    Returns:
        A connection in autocommit mode (``isolation_level=None``) with WAL journalling (skipped
        for ``:memory:``, which SQLite cannot journal to a file), foreign keys enforced, a busy
        timeout, ``synchronous=NORMAL``, and rows returned as :class:`sqlite3.Row` so callers can
        read columns by name.

    Raises:
        sqlite3.OperationalError: The path is not writable or the database file is corrupt.
    """
    # check_same_thread=False: the caller runs this connection's work under asyncio.to_thread,
    # which may use a different worker thread on each call. isolation_level=None puts the
    # connection in autocommit mode so sqlite3 never opens an implicit transaction behind our
    # back; every write then goes through exactly one of two explicit paths: transaction() below
    # for ordinary statements, or hivemind.common.migrations' own pattern for executescript (see
    # transaction()'s docstring for why those two paths cannot be the same one).
    connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    connection.row_factory = sqlite3.Row

    is_memory = str(path) == _MEMORY_PATH
    # WAL lets one writer and many readers proceed concurrently, which is what lets two stores
    # that write the same file (the task store and the pheromone trail sharing one connection's
    # transaction, or two separate connections on the same file) coexist safely. WAL needs a
    # filesystem to place its -wal/-shm files next to; :memory: has none, so it keeps SQLite's
    # default journal mode instead.
    if not is_memory:
        connection.execute("PRAGMA journal_mode = WAL")
    # Foreign keys are off by default in SQLite for backward compatibility; the Hive's schemas
    # (questions referencing tasks, schema_migrations keyed by subsystem) rely on them being
    # enforced so a bug can never leave an orphaned row silently.
    connection.execute("PRAGMA foreign_keys = ON")
    # Matches BUSY_TIMEOUT_MS above: how long SQLite retries internally before raising
    # "database is locked" to the caller.
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # NORMAL is safe under WAL (a crash can lose at most the last commit, never corrupt the
    # database) and considerably faster than FULL, which is the right trade for an audit trail
    # that is also replicated across node segments (codingrules section 12).
    connection.execute("PRAGMA synchronous = NORMAL")

    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run one write transaction on ``connection``, committing on success.

    Issues ``BEGIN IMMEDIATE`` rather than a plain ``BEGIN`` so the write lock is taken up front:
    a deferred transaction only acquires the lock on its first write, which lets two concurrent
    writers both start, both read, and then both discover at write time that they must retry
    ("database is locked" surfacing mid-transaction instead of at the start). Taking the lock
    immediately means a transaction either starts cleanly or fails fast, and it never has to
    upgrade a read lock to a write lock partway through.

    Args:
        connection: An open connection from :func:`connect`. Must not already have a transaction
            in flight on it.

    Yields:
        The same connection, for the caller to issue statements on.

    Raises:
        RuntimeError: ``connection`` already has a transaction in progress (sqlite3 has no nested
            transaction support; the caller must finish or restructure the outer one).
        sqlite3.Error: Re-raised from the wrapped block or from ``COMMIT`` after the transaction
            has been rolled back.

    Note:
        Never call ``connection.executescript(...)`` inside this block; see the module docstring's
        "Key invariants" for why, and ``hivemind.common.migrations`` for the pattern that works.
    """
    # sqlite3.Connection.in_transaction reports whether autocommit mode has an open transaction
    # right now; since connect() always sets isolation_level=None, this is only True while a
    # previous transaction() call on this same connection is still open (a bug in the caller, not
    # a race, because a Python thread only runs one frame of this generator at a time).
    if connection.in_transaction:
        raise RuntimeError(
            "transaction() called on a connection that already has one in progress; "
            "nest transactions by passing the same open connection to the inner call instead"
        )

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        # Any exception, including one that is not a subclass of Exception (e.g. a
        # KeyboardInterrupt during a slow migration script), must still roll back so the
        # connection is never left with a half-applied write; re-raising preserves the original
        # traceback for the caller.
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
