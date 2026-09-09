"""Apply a subsystem's numbered `.sql` migration files and record which versions were applied.

A **migration** is one numbered `.sql` file (`0001_create_pheromone_events.sql`) that moves a
SQLite database's schema forward one step. Every SQLite-backed subsystem (the Pheromone Trail, the
Brood Chamber, and every later store) keeps its own series under `<subsystem>/migrations/`, loaded
here and applied through :func:`apply_migrations`, which records what it applied in one shared
`schema_migrations` table keyed by subsystem name so several subsystems can share a database file
without colliding. This module never invents schema on its own: the SQL text is entirely the
caller's, and this module only sequences and records it.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Called by every subsystem's own
    ``sqlite.py`` composition step (starting with ``hivemind.pheromone.trail.sqlite``), always
    from inside ``asyncio.to_thread`` since every function here is synchronous.

Key invariants:
    - ``load_migrations`` only ever returns a contiguous 1..n run of versions with no gaps and no
      duplicates; a directory that violates that never reaches ``apply_migrations``.
    - ``apply_migrations`` applies each pending migration in exactly one transaction: a script that
      fails leaves neither its own schema changes nor a `schema_migrations` row behind.
    - A version already recorded for a subsystem is never re-applied; if the loaded file's name no
      longer matches the recorded name, that is treated as drift and raises rather than silently
      trusting the newer file.

See Also:
    - .claude/codingrules.md section 11 for the "blocking sqlite3 under asyncio.to_thread" rule.
    - hivemind.common.sqlite for the connection and transaction primitives this module builds on.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources.abc import Traversable
from pathlib import Path

from hivemind.common.errors import MigrationError
from waggle.clock import Clock

# A migration filename is a four-digit version, an underscore, a snake_case name, and ".sql":
# "0001_create_pheromone_events.sql". The four-digit width is a readability convention (directory
# listings sort correctly without it, since we sort by parsed int, but a human scanning the
# directory benefits from fixed-width numbers); it is not itself a limit on how many migrations a
# subsystem may have.
MIGRATION_FILE_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

# Shared by every subsystem so two stores that write the same database file (codingrules section
# 4: "two stores that write the same file use separate connections") still record their applied
# versions without colliding, keyed by the `subsystem` column.
SCHEMA_TABLE = "schema_migrations"

# Kept as a plain (non-f-string) literal, rather than interpolating SCHEMA_TABLE, so ruff's S608
# "possible SQL injection" heuristic (which flags any dynamically built query text regardless of
# whether the interpolated part is a compile-time constant) has nothing to flag here; the literal
# below and SCHEMA_TABLE above are kept in sync by test_migrations.py.
_CREATE_SCHEMA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    subsystem TEXT NOT NULL,
    version INTEGER NOT NULL,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (subsystem, version)
)
"""

__all__ = [
    "MIGRATION_FILE_PATTERN",
    "SCHEMA_TABLE",
    "Migration",
    "applied_versions",
    "apply_migrations",
    "load_migrations",
]


@dataclass(frozen=True, slots=True)
class Migration:
    """One numbered `.sql` migration file, already read off disk.

    Attributes:
        version: The migration's position in its subsystem's series, starting at 1.
        name: The snake_case name segment of the filename (``create_pheromone_events`` from
            ``0001_create_pheromone_events.sql``), compared against the recorded name on every
            later run to detect a renamed file (see the module's "Key invariants").
        sql: The file's full text, executed verbatim by ``sqlite3.Connection.executescript``.
    """

    version: int
    name: str
    sql: str


def load_migrations(location: Path | Traversable) -> tuple[Migration, ...]:
    """Read every migration file directly under ``location``, sorted by version.

    Args:
        location: The migrations directory, either a real path or an ``importlib.resources``
            ``Traversable`` (the usual call is ``importlib.resources.files("hivemind.pheromone
            .migrations")``, so migrations ship inside the installed package). Only entries whose
            name matches :data:`MIGRATION_FILE_PATTERN` are read; anything else (an ``__init__.py``
            package face, a stray README) is ignored.

    Returns:
        Every matching migration, ordered by version ascending.

    Raises:
        MigrationError: Two files share a version, or the versions found are not exactly the
            contiguous run 1..n (a gap, or a series that does not start at 1).
    """
    # dict, not list: lets the duplicate-version check below be a single membership test instead
    # of a second pass over everything read so far.
    by_version: dict[int, Migration] = {}
    for entry in location.iterdir():
        # Directories (and, on a real filesystem, anything else non-file) are never migrations;
        # skipping them here means a stray sub-directory cannot be mistaken for one.
        if not entry.is_file():
            continue
        match = MIGRATION_FILE_PATTERN.match(entry.name)
        if match is None:
            continue
        version = int(match.group(1))
        if version in by_version:
            raise MigrationError(
                f"duplicate migration version {version} under {location}: "
                f"both {by_version[version].name!r} and {match.group(2)!r} claim it"
            )
        by_version[version] = Migration(
            version=version, name=match.group(2), sql=entry.read_text(encoding="utf-8")
        )

    ordered = tuple(by_version[version] for version in sorted(by_version))
    _assert_contiguous(ordered, location)
    return ordered


def applied_versions(connection: sqlite3.Connection, subsystem: str) -> tuple[int, ...]:
    """Return the migration versions already recorded for ``subsystem``.

    Args:
        connection: An open connection from :func:`hivemind.common.sqlite.connect`.
        subsystem: The subsystem name migrations were recorded under, e.g. ``"pheromone"``.

    Returns:
        Recorded versions, ascending. Empty when the schema table does not exist yet (nothing has
        ever been applied on this database) or when it exists but holds no row for ``subsystem``.
    """
    return tuple(_applied_records(connection, subsystem))


def apply_migrations(
    connection: sqlite3.Connection,
    subsystem: str,
    migrations: Sequence[Migration],
    clock: Clock,
) -> tuple[int, ...]:
    """Apply every migration in ``migrations`` that has not yet been recorded for ``subsystem``.

    Creates the shared `schema_migrations` table if it does not already exist, then applies each
    pending migration (version greater than the highest already recorded) in its own transaction:
    ``executescript`` the migration's SQL, then insert its record. A migration whose script raises
    leaves neither the script's own changes nor its record behind (codingrules section 11: one
    transaction per unit of work).

    Args:
        connection: An open connection from :func:`hivemind.common.sqlite.connect`.
        subsystem: The subsystem these migrations belong to, recorded on every row.
        migrations: The full series to reconcile against what is already recorded, typically
            :func:`load_migrations`'s result. Order does not matter; this function sorts by version.
        clock: Injected time source; ``clock.now().isoformat()`` becomes each row's ``applied_at``.

    Returns:
        The versions actually applied by this call, ascending. Empty when every migration in
        ``migrations`` was already recorded.

    Raises:
        MigrationError: A version already recorded for ``subsystem`` has a different name in
            ``migrations`` than the name it was recorded under (the migration file was renamed or
            replaced after shipping).
        sqlite3.Error: A migration's SQL is invalid; the failing migration's transaction is rolled
            back before this propagates, so the database is left exactly as it was beforehand.
    """
    _ensure_schema_table(connection)
    recorded = _applied_records(connection, subsystem)
    _assert_no_name_drift(migrations, recorded)

    highest_applied = max(recorded, default=0)
    # Only versions strictly newer than the highest already recorded are pending; everything
    # below that has already run (and, thanks to the drift check above, still matches).
    pending = sorted(
        (migration for migration in migrations if migration.version > highest_applied),
        key=lambda migration: migration.version,
    )
    for migration in pending:
        _apply_one(connection, subsystem, migration, clock)
    return tuple(migration.version for migration in pending)


def _assert_contiguous(migrations: tuple[Migration, ...], location: Path | Traversable) -> None:
    """Raise unless ``migrations`` is exactly the contiguous run 1..len(migrations)."""
    expected = list(range(1, len(migrations) + 1))
    actual = [migration.version for migration in migrations]
    if actual != expected:
        # A gap or a series not starting at 1 both fail this same comparison; the message shows
        # both lists so either cause is obvious without re-deriving it from the raw filenames.
        raise MigrationError(
            f"migrations under {location} must be numbered contiguously from 1, "
            f"expected versions {expected} but found {actual}"
        )


def _ensure_schema_table(connection: sqlite3.Connection) -> None:
    """Create the shared schema_migrations table if this database does not have one yet.

    Runs outside `transaction()` on purpose: `connect()` puts the connection in autocommit mode,
    so this single idempotent DDL statement commits on its own the moment it executes, with
    nothing else to roll back if it fails.
    """
    connection.execute(_CREATE_SCHEMA_TABLE_SQL)


def _applied_records(connection: sqlite3.Connection, subsystem: str) -> dict[int, str]:
    """Return ``{version: name}`` for every migration recorded for ``subsystem``.

    Returns an empty mapping, rather than raising, when the schema table does not exist yet: a
    database nothing has ever migrated is indistinguishable from one with no applied versions.
    """
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (SCHEMA_TABLE,)
    ).fetchone()
    if table_exists is None:
        return {}

    # SCHEMA_TABLE is a module constant, never caller input, so this f-string carries no injection
    # risk despite matching ruff's S608 pattern; the actual variable part (subsystem) is bound
    # through the "?" placeholder below, not interpolated into the query text.
    rows = connection.execute(
        f"SELECT version, name FROM {SCHEMA_TABLE} "  # noqa: S608 -- table name is a constant
        "WHERE subsystem = ? ORDER BY version",
        (subsystem,),
    ).fetchall()
    return {row["version"]: row["name"] for row in rows}


def _assert_no_name_drift(migrations: Sequence[Migration], recorded: dict[int, str]) -> None:
    """Raise if any migration's name differs from the name it was already recorded under."""
    for migration in migrations:
        recorded_name = recorded.get(migration.version)
        # None means this version has never been applied for this subsystem, which is not drift;
        # only a version that WAS recorded, under a different name, is a renamed/replaced file.
        if recorded_name is not None and recorded_name != migration.name:
            raise MigrationError(
                f"migration {migration.version} was recorded as {recorded_name!r} but the loaded "
                f"file names it {migration.name!r}; a migration file must never change once shipped"
            )


def _apply_one(
    connection: sqlite3.Connection, subsystem: str, migration: Migration, clock: Clock
) -> None:
    """Run one migration's script and record it, atomically.

    Deliberately does not use :func:`hivemind.common.sqlite.transaction`: ``executescript``
    unconditionally commits any transaction already pending the instant it is called (a hard-coded
    behaviour of the sqlite3 C extension, independent of ``isolation_level``), so opening the
    transaction first with a plain ``connection.execute("BEGIN IMMEDIATE")`` -- exactly what
    ``transaction`` does -- would have it committed away, empty, before the script's own statements
    ever ran; each of them would then autocommit individually instead of atomically, and the
    ``COMMIT`` a context manager would issue afterwards would fail outright ("cannot commit - no
    transaction is active"), because nothing would still be open. The verified-safe shape instead
    makes the script's own text open the transaction as its first statement, so nothing is pending
    when ``executescript`` is called and the implicit-commit rule never fires; the transaction is
    then left open (no ``COMMIT`` in the text) so the parametrised INSERT below joins the very same
    transaction the script started, and this function closes it explicitly.
    """
    if _in_transaction(connection):
        # Mirrors sqlite.transaction()'s own guard: a bug in the caller (e.g. calling this from
        # inside another open transaction on the same connection), not a race, since sqlite3
        # connections are only ever driven from one thread at a time.
        raise RuntimeError(
            f"cannot apply migration {migration.version} for subsystem {subsystem!r}: "
            "connection already has a transaction in progress"
        )

    try:
        # BEGIN IMMEDIATE as literal script text (not a prior connection.execute call) is what
        # keeps executescript's implicit-commit rule from firing; see the docstring above.
        connection.executescript(f"BEGIN IMMEDIATE;\n{migration.sql}")
        # SCHEMA_TABLE is a module constant; see the identical SAFETY note on _applied_records.
        connection.execute(
            f"INSERT INTO {SCHEMA_TABLE} "  # noqa: S608 -- table name is a constant
            "(subsystem, version, name, applied_at) VALUES (?, ?, ?, ?)",
            (subsystem, migration.version, migration.name, clock.now().isoformat()),
        )
    except BaseException:
        # The script or the insert failed partway. Usually the script's own BEGIN IMMEDIATE
        # already opened a transaction that is still active (nothing above issues its own
        # COMMIT), but a failure so early that BEGIN IMMEDIATE itself never ran (e.g. the busy
        # timeout expired first) would leave none open; guard so ROLLBACK is only issued when
        # there is really something to undo, so a broken migration leaves neither its own schema
        # changes nor a schema_migrations row.
        if _in_transaction(connection):
            connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def _in_transaction(connection: sqlite3.Connection) -> bool:
    """Read `connection.in_transaction` through a call, not a direct attribute read.

    Purely a mypy workaround: `--strict` narrows a direct `connection.in_transaction` read to
    `Literal[False]` after an earlier `if connection.in_transaction: raise ...` guard in the same
    function and does not invalidate that narrowing across the `execute`/`executescript` calls in
    between, even though either can genuinely open a transaction; routing the read through an
    opaque function call defeats that (incorrect) narrowing.
    """
    return connection.in_transaction
