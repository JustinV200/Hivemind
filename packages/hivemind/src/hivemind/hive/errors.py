"""Define HiveError and the ways a Virtual Cell or its backend can fail on purpose.

The hive package (`hivemind.hive`, lowercase, distinct from the Hive as a whole) provisions and
destroys Virtual Cells (VM or container Cells the Queen owns, not borrows). This module is that
package's own error tree, rooted at `HiveError`, so a caller several layers up can catch one name
and know it caught anything the hive package raised on purpose (codingrules section 10).
`CellProvisionError` and `CellDestroyError` map onto no base category in `hivemind.common.errors`:
a backend refusing (or failing) to create or tear down infrastructure is specific to a
`hivemind.hive.backends.base.CellBackend`, the same way `hivemind.cell.errors.
SnapshotUnsupportedError` and `ProbeError` subclass `CellError` directly for the same reason.
`UnknownBackendError` is a
`ConfigurationError` (a `[hive] backend` name the manifest names but nothing registered --
`hivemind.common.errors.ConfigurationError`'s own docstring names this exact class as its example).
`InvalidCellTransitionError` is a `ConflictError`, mirroring `hivemind.cell.errors.
InvalidLeaseTransitionError` for a different state machine (`hivemind.hive.cell_state.
TRANSITIONS`). `BackendCapabilityError` covers a `CellBackend.pause`/`resume` call a backend's own
declared `BackendCapabilities` says it cannot honour -- "pause/resume ... cleanly refused per
capabilities" (roadmap step 5.2's contract suite) -- the same capability-refusal shape
`SnapshotUnsupportedError` gives Real Cells; it is not one of the four names roadmap step 5.2 lists
by name, added because `CellBackend.pause`/`resume` (also step 5.2) need a typed way to refuse.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down). Raised by every other module in this
    package (`models.py`, `cell_state.py`, `registry.py`, `backends/base.py`, `backends/fake.py`)
    and read by every layer above that provisions, destroys, pauses or resumes a Virtual Cell.

Key invariants:
    - Every HiveError subclass sets its own `code`; none shares a code with another.
    - InvalidCellTransitionError takes `from_status`/`to_status` typed as plain `Enum`, not
      `VirtualCellStatus`, so this module never imports `hivemind.hive.cell_state` -- that module
      imports this one for the error it raises, and a reverse import would cycle the two files.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError and the base categories this module's classes
      descend from.
    - hivemind.cell.errors for InvalidLeaseTransitionError and SnapshotUnsupportedError, the
      patterns InvalidCellTransitionError and BackendCapabilityError mirror.
    - hivemind.hive.cell_state for VirtualCellStatus and TRANSITIONS, the table
      InvalidCellTransitionError reports a forbidden edge in.
    - hivemind.hive.backends.base for CellBackend and BackendCapabilities, whose contract these
      errors report a violation of.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import ClassVar

from hivemind.common.errors import ConfigurationError, ConflictError, HiveMindError

__all__ = [
    "BackendCapabilityError",
    "CellDestroyError",
    "CellProvisionError",
    "HiveError",
    "InvalidCellTransitionError",
    "UnknownBackendError",
]


class HiveError(HiveMindError):
    """Root of every error `hivemind.hive` raises on purpose.

    Subclass this for a specific failure, as the classes below do; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.hive.error"


class CellProvisionError(HiveError):
    """Raise when a CellBackend cannot create a Virtual Cell, or it never became reachable.

    `hivemind.hive.backends.base.CellBackend.provision` raises this instead of leaving a
    half-created Cell behind (its own key invariant); the backend has already cleaned up whatever
    it started by the time this is raised.
    """

    code: ClassVar[str] = "hivemind.hive.cell_provision_failed"

    def __init__(self, backend_name: str, image: str, reason: str) -> None:
        """Build the error for a Virtual Cell that could not be provisioned.

        Args:
            backend_name: The CellBackend.name that refused or failed.
            image: The `VirtualCellSpec.image` provisioning was attempted from.
            reason: Why it failed, folded into the message so the failure is debuggable without a
                stack trace (e.g. "did not become reachable within 60.0s").
        """
        super().__init__(
            f"Backend {backend_name!r} could not provision a Cell from image {image!r}: {reason}."
        )
        self.backend_name = backend_name
        self.image = image
        self.reason = reason


class CellDestroyError(HiveError):
    """Raise when a CellBackend acknowledges a Cell exists but cannot remove it.

    Not raised for an unknown or already-destroyed id: `CellBackend.destroy` is idempotent and
    returns silently for those (codingrules Appendix A.1). The caller (the Undertaker, the cleanup
    Worker) should record this on the Pheromone Trail and retry later.
    """

    code: ClassVar[str] = "hivemind.hive.cell_destroy_failed"

    def __init__(self, backend_name: str, cell_id: str, reason: str) -> None:
        """Build the error for a Virtual Cell that could not be destroyed.

        Args:
            backend_name: The CellBackend.name that refused or failed.
            cell_id: The `CellId` destruction was attempted on.
            reason: Why it failed, folded into the message.
        """
        super().__init__(f"Backend {backend_name!r} could not destroy cell {cell_id!r}: {reason}.")
        self.backend_name = backend_name
        self.cell_id = cell_id
        self.reason = reason


class UnknownBackendError(ConfigurationError):
    """Raise when `hivemind.hive.registry.BackendRegistry.get` is asked for an unregistered name.

    Covers a `[hive] backend` manifest value with no matching `register()` call from the
    composition root.
    """

    code: ClassVar[str] = "hivemind.hive.unknown_backend"

    def __init__(self, name: str, known: Sequence[str]) -> None:
        """Build the error for an unregistered backend name.

        Args:
            name: The backend name that was looked up.
            known: Every name actually registered, folded into the message so a typo in the
                manifest is obvious without reading the registry's source.
        """
        super().__init__(
            f"No CellBackend is registered under {name!r}; known backends: {sorted(known)!r}."
        )
        self.name = name
        self.known = tuple(known)


class InvalidCellTransitionError(ConflictError):
    """Raise when `hivemind.hive.cell_state` is asked for an edge its table does not have.

    `from_status`/`to_status` are typed `Enum` rather than `VirtualCellStatus` so this module
    never imports `hivemind.hive.cell_state` (see the module docstring's key invariant). Also
    raised by `hivemind.hive.cell_state.assert_dormant_allowed` for a NIGHT_VEIL Cell asked to
    enter DORMANT, a forbidden move for a different reason than a missing table entry (codingrules
    section 8.7: Night Veil is teardown-only), with `reason` overriding the default message.
    """

    code: ClassVar[str] = "hivemind.hive.invalid_cell_transition"

    def __init__(
        self,
        from_status: Enum,
        to_status: Enum,
        cell_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        """Build the error for a forbidden Virtual Cell state transition.

        Args:
            from_status: The state the Cell was in.
            to_status: The state a caller asked to move it to.
            cell_id: The Cell's id, when the caller has it, folded into the message so the
                failure is debuggable without a stack trace.
            reason: Overrides the default "no such edge" explanation, for a transition that is
                forbidden by an invariant beyond the table itself (e.g. Night Veil and DORMANT).
        """
        subject = f" for cell {cell_id}" if cell_id is not None else ""
        detail = (
            reason
            if reason is not None
            else "no such edge exists in the Virtual Cell state machine"
        )
        message = (
            f"Cannot transition{subject} from {from_status.name} to {to_status.name}: {detail}."
        )
        super().__init__(message)
        self.from_status = from_status
        self.to_status = to_status
        self.cell_id = cell_id


class BackendCapabilityError(HiveError):
    """Raise when a CellBackend is asked to do something its own BackendCapabilities refuses.

    Covers `pause`/`resume` on a backend whose `capabilities.can_pause` is False (roadmap step
    5.2's contract suite: "pause/resume honoured or cleanly refused per capabilities"), the same
    capability-refusal shape `hivemind.cell.errors.SnapshotUnsupportedError` gives Real Cells.
    """

    code: ClassVar[str] = "hivemind.hive.backend_capability_unsupported"

    def __init__(self, backend_name: str, capability: str, cell_id: str | None = None) -> None:
        """Build the error for an unsupported backend capability.

        Args:
            backend_name: The CellBackend.name that refused.
            capability: Which capability was missing ("pause", "resume", ...).
            cell_id: The Cell the call named, when there is one.
        """
        subject = f" on cell {cell_id!r}" if cell_id is not None else ""
        super().__init__(f"Backend {backend_name!r} does not support {capability}{subject}.")
        self.backend_name = backend_name
        self.capability = capability
        self.cell_id = cell_id
