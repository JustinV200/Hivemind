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
    - hivemind.common.errors, hivemind.common.logging, hivemind.common.result for the modules
      behind this package's public API.

Public API:
    - HiveMindError: root of every error a hivemind subsystem raises on purpose.
    - ConfigurationError, NotFoundError, ConflictError, PermissionDeniedError,
      DeadlineExceededError, InvariantViolationError: the base error categories subsystems extend.
    - configure_logging: one-time structlog configuration, called only by a composition root.
    - get_logger: hand a subsystem its own bound structlog logger.
    - Ok, Err, Result, ResultUnwrapError: the Result type used only at the LLM boundary.
"""

from hivemind.common.errors import (
    ConfigurationError,
    ConflictError,
    DeadlineExceededError,
    HiveMindError,
    InvariantViolationError,
    NotFoundError,
    PermissionDeniedError,
)
from hivemind.common.logging import configure_logging, get_logger
from hivemind.common.result import Err, Ok, Result, ResultUnwrapError

__all__ = [
    "ConfigurationError",
    "ConflictError",
    "DeadlineExceededError",
    "Err",
    "HiveMindError",
    "InvariantViolationError",
    "NotFoundError",
    "Ok",
    "PermissionDeniedError",
    "Result",
    "ResultUnwrapError",
    "configure_logging",
    "get_logger",
]
