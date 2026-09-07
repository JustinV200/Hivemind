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

Schema migrations are not implemented yet; they land in a later roadmap step.

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
