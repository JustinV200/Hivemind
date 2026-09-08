# hivemind.common

The common package holds Layer 0 primitives shared by every other hivemind subsystem: shared
error types, a Result type for fallible operations, logging setup and schema migrations. It
imports nothing internal to hivemind beyond waggle's ids, clock and loop shape, and it never
grows domain logic of its own.

## Public API (roadmap step 0.5)

- **Errors** (`hivemind.common.errors`): `HiveMindError`, the root every subsystem's own error
  tree descends from, carrying a stable dotted `code` class attribute. Six base categories to
  extend: `ConfigurationError`, `NotFoundError`, `ConflictError`, `PermissionDeniedError`,
  `DeadlineExceededError`, `InvariantViolationError`.
- **Logging** (`hivemind.common.logging`): `configure_logging(*, json_output, level)`, called
  exactly once by a composition root (a CLI entry point, the Entrance's startup, or a test acting
  as one); `get_logger(name)` hands any subsystem a bound `structlog` logger. Importing this
  module configures nothing on its own (codingrules section 5.5).
- **Result** (`hivemind.common.result`): `Ok`, `Err` and the `Result` type alias, used only at the
  LLM boundary (codingrules section 9), where a provider call failing is expected, not a bug.
  `unwrap()`/`unwrap_err()` raise `ResultUnwrapError` when called on the wrong variant.
- **SQLite** (`hivemind.common.sqlite`): `connect(path)` opens a `sqlite3.Connection` with the
  Hive's standard pragmas (WAL except for `:memory:`, foreign keys on, a busy timeout, `NORMAL`
  synchronous, `sqlite3.Row` rows) and `isolation_level=None` so transactions are always explicit.
  `transaction(connection)` is the sync context manager every ordinary write uses: `BEGIN
  IMMEDIATE`, `COMMIT` on success, `ROLLBACK` and re-raise on any exception, and a clear
  `RuntimeError` if the connection already has one in flight. It must never wrap a call to
  `connection.executescript(...)` — see its docstring and `hivemind.common.migrations` for why.
  Every function here is synchronous; codingrules section 11 requires callers to run it under
  `asyncio.to_thread`.
- **Migrations** (`hivemind.common.migrations`): `load_migrations(location)` reads every
  `NNNN_name.sql` file directly under a directory or an `importlib.resources` `Traversable`
  (`MIGRATION_FILE_PATTERN`), requiring the versions found to be exactly the contiguous run 1..n
  with no duplicates. `apply_migrations(connection, subsystem, migrations, clock)` creates the
  shared `schema_migrations` table (`SCHEMA_TABLE`) if missing, then applies every migration newer
  than the highest already recorded for `subsystem`, each in its own atomic transaction, and
  returns the versions it applied. `applied_versions(connection, subsystem)` reads back what has
  run, returning an empty tuple before anything ever has. A migration already recorded under a
  different name raises `MigrationError` naming both. Because `executescript` cannot be safely
  wrapped in `hivemind.common.sqlite.transaction` (it unconditionally commits any transaction
  already pending the instant it is called), `apply_migrations` gets one-transaction-per-migration
  atomicity by making each migration's own script text open its transaction as its first
  statement instead; see `_apply_one`'s docstring for the full explanation.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/common
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.common uv run --frozen pytest -p no:cacheprovider --cov=hivemind.common \
    --cov-report=term-missing packages/hivemind/tests/unit/common
```

`test_logging.py` calls `configure_logging` itself (a test is a composition root) and reads its
output back with `structlog.testing.capture_logs`, so no test depends on process-wide logging
state left over from another test.

`test_sqlite.py` and `test_migrations.py` use `tmp_path` for every on-disk database (never a
shared fixture file) and `waggle.clock.FakeClock` wherever a timestamp is asserted, so
`applied_at` values are deterministic instead of depending on wall-clock time.
