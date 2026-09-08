"""Define HiveMindError, the root exception every hivemind subsystem's own errors descend from.

Every subsystem in the Hive (the Queen, the central orchestrator; a Warden, a Cell's supervisor;
the Brood Chamber, the task store; and so on) defines its own error subclass tree in its own
``errors.py``, but every one of those trees is rooted here so a caller several layers up can catch
``HiveMindError`` and know it caught anything the Hive itself raised on purpose. Each error carries
a stable, dotted, lowercase ``code`` class attribute, the same shape Waggle's own ``ErrorMessage``
will carry once it crosses the wire in phase 1, so an error is identifiable by code alone even
after its Python type is lost to serialisation. Below the root, six base categories cover the
kinds of failure almost every subsystem eventually needs; a subsystem raises one of these directly
only when it has nothing more specific to say, and subclasses it otherwise.

Fits into the Hive:
    Layer 0 (primitives; imports nothing internal beyond waggle). Imported by every layer above
    it, each of which defines its own ``<subsystem>/errors.py`` subclassing one of these.

Key invariants:
    - Every HiveMindError subclass sets its own ``code``; two unrelated error classes should not
      share one.
    - HiveMindError never inherits from waggle.errors.WaggleError, and vice versa: the two error
      trees are separate because waggle cannot import hivemind (codingrules section 10).

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - waggle.errors for the equivalent root on the waggle side.
"""

from __future__ import annotations

from typing import ClassVar

__all__ = [
    "ConfigurationError",
    "ConflictError",
    "DeadlineExceededError",
    "HiveMindError",
    "InvariantViolationError",
    "NotFoundError",
    "PermissionDeniedError",
]


class HiveMindError(Exception):
    """Root of every error a hivemind subsystem raises on purpose.

    Subsystems never raise this directly; they raise one of the six categories below, or a more
    specific subclass of one of those, defined in their own ``<subsystem>/errors.py``.
    """

    #: Dotted, lowercase, stable identifier for this error, independent of its Python class name.
    code: ClassVar[str] = "hivemind.error"


class ConfigurationError(HiveMindError):
    """Raise when a Hive Manifest (the Hive's TOML config) is missing, malformed, or invalid.

    Subclass this in a subsystem's own errors.py for a specific missing key or bad value, e.g.
    ``UnknownBackendError(ConfigurationError)`` in hivemind.hive.
    """

    code: ClassVar[str] = "hivemind.configuration_error"


class NotFoundError(HiveMindError):
    """Raise when a lookup by id (a Task, a Cell, a Worker, ...) finds nothing.

    Subclass this for a specific missing resource, e.g. ``CellNotFoundError(NotFoundError)`` in
    hivemind.cell.
    """

    code: ClassVar[str] = "hivemind.not_found"


class ConflictError(HiveMindError):
    """Raise when an operation would violate a uniqueness or state constraint.

    Example: leasing a Real Cell (an existing, borrowed device) that another lease already holds.
    """

    code: ClassVar[str] = "hivemind.conflict"


class PermissionDeniedError(HiveMindError):
    """Raise when the caller lacks the AccessLevel or capability an operation requires.

    Guard (the Hive's policy engine) and every enforcement point above it subclass this.
    """

    code: ClassVar[str] = "hivemind.permission_denied"


class DeadlineExceededError(HiveMindError):
    """Raise when an operation misses its timeout instead of completing in time.

    Every external await from codingrules section 11 that times out should surface as this or a
    subclass of it, e.g. ``CellNotReadyError(DeadlineExceededError)`` in hivemind.hive.
    """

    code: ClassVar[str] = "hivemind.deadline_exceeded"


class InvariantViolationError(HiveMindError):
    """Raise when code detects that one of its own documented invariants no longer holds.

    This signals a bug inside the Hive itself, not bad caller input; subclass it for the specific
    invariant that broke so the Pheromone Trail (the Hive's audit log) records which one.
    """

    code: ClassVar[str] = "hivemind.invariant_violation"
