"""Define CellError and the ways a Cell, a lease or a session can be misused or can fail on purpose.

The cell package (`hivemind.cell`) defines the Cell abstraction: what a Cell is, a terminal
session on it, and the leases a Real Cell (an existing device the Hive borrows and leaves exactly
as found) is opened and closed through. This module is that package's own error tree, rooted at
`CellError`, so a caller several layers up can catch one name and know it caught anything the Cell
abstraction itself raised on purpose (codingrules section 10). Most of these map onto one of
`hivemind.common.errors`' six base categories: a refused lease is a `ConflictError` (another lease
already holds the Cell), a closed session or a path outside a lease's reach are permission-shaped
failures, a missed command deadline is a `DeadlineExceededError`, and an illegal lease-state edge
is a `ConflictError` for the same reason `brood_chamber.errors.InvalidTransitionError` is.
`SnapshotUnsupportedError` and `ProbeError` do not map onto any base category -- one names a
capability a Real Cell never has, the other a host this process cannot describe -- so both
subclass `CellError` directly, the same way `guard.errors.InvalidCapabilityError` subclasses
`GuardError` directly for the same reason.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by every other module in this
    package (`models.py`, `session.py`, `lease_state.py`, `lease.py`, `snapshot.py`, `fake/`,
    `local/`) and read by every layer above that leases a Cell, opens a session on one, or asks a
    Snapshotter to act on one.

Key invariants:
    - Every CellError subclass sets its own `code`; none shares a code with another.
    - InvalidLeaseTransitionError takes `from_state`/`to_state` typed as plain `Enum`, not
      `LeaseState`, so this module never imports `hivemind.cell.lease_state` -- that module
      imports this one for the error it raises, and a reverse import would cycle the two files.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError and the six base categories this module descends
      most of its classes from.
    - hivemind.cell.lease_state for LeaseState and TRANSITIONS, the table
      InvalidLeaseTransitionError reports a forbidden edge in.
    - hivemind.brood_chamber.errors for InvalidTransitionError, the pattern this module's
      InvalidLeaseTransitionError mirrors for a different state machine.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import ClassVar

from hivemind.common.errors import (
    ConflictError,
    DeadlineExceededError,
    HiveMindError,
    PermissionDeniedError,
)

__all__ = [
    "CellError",
    "CommandTimeoutError",
    "InvalidLeaseTransitionError",
    "LeaseRefusedError",
    "PathNotAllowedError",
    "ProbeError",
    "ScratchQuotaExceededError",
    "SessionClosedError",
    "SnapshotUnsupportedError",
]


class CellError(HiveMindError):
    """Root of every error `hivemind.cell` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.cell.error"


class LeaseRefusedError(ConflictError):
    """Raise when a `RealCellSource` refuses to open a lease.

    Covers an unknown `cell_id`, a Cell another lease already holds, and a source that is
    disabled (`hivemind.cell.local`'s `[hive_stand] enabled = false`).
    """

    code: ClassVar[str] = "hivemind.cell.lease_refused"

    def __init__(self, cell_id: str, reason: str) -> None:
        """Build the error for a refused lease request.

        Args:
            cell_id: The `CellId` the request named.
            reason: Why the source refused it, folded into the message so the failure is
                debuggable without a stack trace.
        """
        super().__init__(f"Lease refused on cell {cell_id!r}: {reason}.")
        self.cell_id = cell_id
        self.reason = reason


class SessionClosedError(CellError):
    """Raise when `exec`, `put_file` or `get_file` is called on a closed `CellSession`."""

    code: ClassVar[str] = "hivemind.cell.session_closed"

    def __init__(self, scratch_dir: Path) -> None:
        """Build the error for an operation attempted on a closed session.

        Args:
            scratch_dir: The closed session's own scratch directory, identifying which session.
        """
        super().__init__(
            f"The Cell session scoped to {scratch_dir} is closed; open a new session first."
        )
        self.scratch_dir = scratch_dir


class CommandTimeoutError(DeadlineExceededError):
    """Raise when `CellSession.exec` does not finish within `ExecSpec.timeout_s`."""

    code: ClassVar[str] = "hivemind.cell.command_timeout"

    def __init__(self, argv: tuple[str, ...], timeout_s: float) -> None:
        """Build the error for a command that missed its deadline.

        Args:
            argv: The command that timed out, exactly as it was passed to `ExecSpec`.
            timeout_s: The deadline it missed, in seconds.
        """
        super().__init__(f"Command {argv!r} did not finish within {timeout_s}s.")
        self.argv = argv
        self.timeout_s = timeout_s


class PathNotAllowedError(PermissionDeniedError):
    """Raise when a put_file/get_file path resolves outside scratch and every allowed path."""

    code: ClassVar[str] = "hivemind.cell.path_not_allowed"

    def __init__(self, path: Path) -> None:
        """Build the error for a path a session refuses to touch.

        Args:
            path: The resolved (absolute, `..`-free, symlink-followed) path that was refused.
        """
        super().__init__(
            f"Path {path} is outside the session's scratch directory and its allowed paths."
        )
        self.path = path


class SnapshotUnsupportedError(CellError):
    """Raise when `Snapshotter.rollback` is asked to roll back a Cell it can never roll back.

    `NoopSnapshotter` (`hivemind.cell.snapshot`) raises this from every `rollback` call: a Real
    Cell (an existing, borrowed device) has no rollback mechanism the Hive controls (codingrules
    section 8.7).
    """

    code: ClassVar[str] = "hivemind.cell.snapshot_unsupported"

    def __init__(self, cell_id: str) -> None:
        """Build the error for a Cell with no rollback mechanism.

        Args:
            cell_id: The `CellId` rollback was attempted on.
        """
        super().__init__(
            f"Cell {cell_id!r} cannot be rolled back: it has no snapshot mechanism (Real Cells "
            "are borrowed, not snapshotted)."
        )
        self.cell_id = cell_id


class InvalidLeaseTransitionError(ConflictError):
    """Raise when `hivemind.cell.lease_state` is asked for an edge its table does not have.

    `from_state`/`to_state` are typed `Enum` rather than `LeaseState` so this module never
    imports `hivemind.cell.lease_state` (see the module docstring's key invariant).
    """

    code: ClassVar[str] = "hivemind.cell.invalid_lease_transition"

    def __init__(self, from_state: Enum, to_state: Enum, lease_id: str | None = None) -> None:
        """Build the error for a forbidden lease-state transition.

        Args:
            from_state: The state the lease was in.
            to_state: The state a caller asked to move it to.
            lease_id: The lease's id, when the caller has it, folded into the message so the
                failure is debuggable without a stack trace.
        """
        # A missing lease_id still produces a full sentence; codingrules section 10 wants the
        # ids needed to debug, not a placeholder, so the clause is only added when one is known.
        subject = f" for lease {lease_id}" if lease_id is not None else ""
        message = (
            f"Cannot transition{subject} from {from_state.name} to {to_state.name}: no such "
            "edge exists in the lease state machine."
        )
        super().__init__(message)
        self.from_state = from_state
        self.to_state = to_state
        self.lease_id = lease_id


class ProbeError(CellError):
    """Raise when this host is too unusable to describe: `cell.local.probe` finds 0 cores."""

    code: ClassVar[str] = "hivemind.cell.probe_error"

    def __init__(self, reason: str) -> None:
        """Build the error for a host that cannot be probed at all.

        Args:
            reason: What made the host unusable, folded into the message.
        """
        super().__init__(f"Cannot probe this host's capabilities: {reason}.")
        self.reason = reason


class ScratchQuotaExceededError(CellError):
    """Raise when a lease's scratch directory grows past its configured quota mid-command.

    Raised by `hivemind.cell.local.session.LocalProcessSession.exec`'s watchdog once it has
    already killed the offending command's process tree; the caller (eventually a Worker, phase
    3 step 3.15+) turns this into a `QUOTA_EXCEEDED` Alarm (roadmap step 3.11).
    """

    code: ClassVar[str] = "hivemind.cell.scratch_quota_exceeded"

    def __init__(self, scratch_dir: Path, quota_bytes: int, observed_bytes: int) -> None:
        """Build the error for a scratch directory that outgrew its quota.

        Args:
            scratch_dir: The lease's scratch directory that exceeded its quota.
            quota_bytes: The configured cap it exceeded.
            observed_bytes: The size the watchdog measured when it killed the command.
        """
        super().__init__(
            f"Scratch directory {scratch_dir} grew to {observed_bytes} bytes, over its "
            f"{quota_bytes}-byte quota; the command was killed."
        )
        self.scratch_dir = scratch_dir
        self.quota_bytes = quota_bytes
        self.observed_bytes = observed_bytes
