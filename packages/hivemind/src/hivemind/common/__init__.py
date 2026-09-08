"""Hold Layer 0 primitives shared by every hivemind subsystem: the common package.

Shared error types, a Result type for fallible operations, logging setup and schema migrations
live here. It imports nothing internal to hivemind beyond waggle's ids, clock and loop shape, and
it never grows domain logic of its own.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal). Called by every layer above it. Calls into
    waggle's ids, clock and loop shape only.

Key invariants:
    - Nothing here imports another hivemind subsystem, directly or transitively (enforced by the
      "common imports nothing else in hivemind" import-linter contract).

See Also:
    - .claude/codingrules.md section 4 for the layer 0 row this package occupies.
    - hivemind.common.errors, hivemind.common.logging, hivemind.common.migrations,
      hivemind.common.result, hivemind.common.sqlite for the modules behind this package's public
      API.

Public API:
    - HiveMindError: root of every error a hivemind subsystem raises on purpose.
    - ConfigurationError, NotFoundError, ConflictError, PermissionDeniedError,
      DeadlineExceededError, InvariantViolationError, MigrationError: the base error categories
      subsystems extend, plus the migrations-specific error.
    - configure_logging: one-time structlog configuration, called only by a composition root.
    - get_logger: hand a subsystem its own bound structlog logger.
    - Ok, Err, Result, ResultUnwrapError: the Result type used only at the LLM boundary.
    - connect, transaction, BUSY_TIMEOUT_MS: open a pragma-configured SQLite connection and run
      one explicit transaction on it.
    - Migration, load_migrations, applied_versions, apply_migrations, MIGRATION_FILE_PATTERN,
      SCHEMA_TABLE: apply a subsystem's numbered `.sql` migration series and record what ran.
"""

from hivemind.common.errors import (
    ConfigurationError,
    ConflictError,
    DeadlineExceededError,
    HiveMindError,
    InvariantViolationError,
    MigrationError,
    NotFoundError,
    PermissionDeniedError,
)
from hivemind.common.logging import configure_logging, get_logger
from hivemind.common.migrations import (
    MIGRATION_FILE_PATTERN,
    SCHEMA_TABLE,
    Migration,
    applied_versions,
    apply_migrations,
    load_migrations,
)
from hivemind.common.result import Err, Ok, Result, ResultUnwrapError
from hivemind.common.sqlite import BUSY_TIMEOUT_MS, connect, transaction

__all__ = [
    "BUSY_TIMEOUT_MS",
    "MIGRATION_FILE_PATTERN",
    "SCHEMA_TABLE",
    "ConfigurationError",
    "ConflictError",
    "DeadlineExceededError",
    "Err",
    "HiveMindError",
    "InvariantViolationError",
    "Migration",
    "MigrationError",
    "NotFoundError",
    "Ok",
    "PermissionDeniedError",
    "Result",
    "ResultUnwrapError",
    "applied_versions",
    "apply_migrations",
    "configure_logging",
    "connect",
    "get_logger",
    "load_migrations",
    "transaction",
]
