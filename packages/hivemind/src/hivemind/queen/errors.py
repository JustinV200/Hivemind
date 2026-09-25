"""Define QueenError and the ways the Queen's own bookkeeping can fail on purpose.

The Queen (`hivemind.queen.queen.Queen`, the Hive's single orchestrator and only global view) can
fail in a few ways this package raises on purpose: a `Supervisor.telemetry`/`inspect`/`intervene`
call names a Warden the Queen does not currently supervise (`UnknownWardenError`), or her own set
does not hold `warden:spawn` when a Warden asks to be attached (`WardenSpawnRefusedError`, roadmap
step 10.3; the refusal is already on the trail as `guard.denied` when this is raised). Roadmap
step 10.6a adds the human's isolation levers' four refusals: a Cell she has no Warden attached for
(`UnknownCellError`), a Guard report she was never sent (`UnknownGuardReportError`), a lift of a
Cell that is not isolated (`CellNotIsolatedError`), and an isolation the `isolation` enforcement
point refused (`IsolationRefusedError`). Every
subsystem roots its own error tree at `hivemind.common.errors.HiveMindError` (codingrules section
10); this module is `hivemind.queen`'s own root plus its specific subclasses. `PlacementError` is a
sibling of this tree but lives in `hivemind.queen.placement.decide` instead, next to the one
function that raises it, per this dispatch's own instructions.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Raised by
    `hivemind.queen.queen.Queen` (the `Supervisor` methods over Wardens) and `hivemind.queen.
    attach` (Warden spawn); caught by whichever caller can recover. Calls into
    `hivemind.common.errors` only.

Key invariants:
    - Every QueenError subclass sets its own `code`; none shares a code with another.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - hivemind.common.errors for HiveMindError and NotFoundError, the roots this module's classes
      descend from.
    - hivemind.queen.placement.decide for PlacementError, this tree's other member.
    - hivemind.queen.queen for Queen, the one raiser of UnknownWardenError.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import (
    ConflictError,
    HiveMindError,
    NotFoundError,
    PermissionDeniedError,
)

__all__ = [
    "CellNotIsolatedError",
    "IsolationRefusedError",
    "QueenError",
    "UnknownCellError",
    "UnknownGuardReportError",
    "UnknownWardenError",
    "WardenSpawnRefusedError",
]


class QueenError(HiveMindError):
    """Root of every error `hivemind.queen` raises on purpose.

    Subclass this for a specific failure, as the class below does; code that has nothing more
    specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.queen.error"


class UnknownWardenError(NotFoundError):
    """Raise when a Supervisor call names a Warden this Queen does not currently supervise.

    Raised by `hivemind.queen.queen.Queen.telemetry`/`.inspect`/`.intervene`
    (`hivemind.supervision.supervisor.Supervisor`'s own contract), mirroring
    `hivemind.wardens.errors.UnknownSubBeeError` field for field.
    """

    code: ClassVar[str] = "hivemind.queen.unknown_warden"

    def __init__(self, warden_id: str) -> None:
        """Build the error for a Warden id this Queen has never attached or has since dropped.

        Args:
            warden_id: The id a caller passed that names no attached Warden.
        """
        super().__init__(f"Queen supervises no Warden {warden_id}.")
        self.warden_id = warden_id


class WardenSpawnRefusedError(PermissionDeniedError):
    """Raise when the Queen's own set does not allow another Warden to be attached.

    Raised by `hivemind.queen.attach.attach_warden` at the `warden_spawn` enforcement point
    (roadmap step 10.3) after the Guard's `Enforcer` has already recorded `guard.denied`; the
    caller (the composition root, or the Virtual Cell listener) leaves the Warden unattached.
    """

    code: ClassVar[str] = "hivemind.queen.warden_spawn_refused"

    def __init__(self, warden_id: str, reason: str) -> None:
        """Build the error for a Warden the Queen was refused leave to attach.

        Args:
            warden_id: The Warden that asked to be attached.
            reason: The Guard's own reason sentence, naming the rule that refused.
        """
        super().__init__(f"Warden {warden_id} was not attached: {reason}")
        self.warden_id = warden_id


class UnknownCellError(NotFoundError):
    """Raise when an isolation names a Cell no Warden attached to this Queen runs (step 10.6a).

    The Queen isolates through the Cell's own Warden (its grant, its bees), so a Cell she does not
    supervise right now is out of her reach; the Hive Entrance answers 404.
    """

    code: ClassVar[str] = "hivemind.queen.unknown_cell"

    def __init__(self, cell_id: str) -> None:
        """Build the error for a Cell no attached Warden runs.

        Args:
            cell_id: The Cell the caller named.
        """
        super().__init__(f"No Warden attached to this Queen runs Cell {cell_id}.")
        self.cell_id = cell_id


class UnknownGuardReportError(NotFoundError):
    """Raise when the human's isolation cites a Guard report no request ever filed (step 10.6a).

    A cited report is where the Cell's memory stops being trusted (its first event), so one the
    Queen never received cannot be cited; the Hive Entrance answers 404 and nothing is isolated.
    """

    code: ClassVar[str] = "hivemind.queen.unknown_guard_report"

    def __init__(self, report_id: str) -> None:
        """Build the error for a report id no Guard request carries.

        Args:
            report_id: The report the caller cited.
        """
        super().__init__(f"No Guard request filed with this Queen carries report {report_id}.")
        self.report_id = report_id


class CellNotIsolatedError(ConflictError):
    """Raise when the human asks to lift a Cell that is neither isolated nor held (step 10.6a).

    Lifting is the human's lever alone; a lift with nothing to lift changes nothing and says so,
    so the Hive Entrance answers 409 rather than recording a lift that never happened.
    """

    code: ClassVar[str] = "hivemind.queen.cell_not_isolated"

    def __init__(self, cell_id: str) -> None:
        """Build the error for a Cell with no isolation and no placement hold to lift.

        Args:
            cell_id: The Cell the human named.
        """
        super().__init__(f"Cell {cell_id} is not isolated and holds no placement.")
        self.cell_id = cell_id


class IsolationRefusedError(PermissionDeniedError):
    """Raise when the `isolation` enforcement point refused an isolation the human asked for.

    The refusal is already on the trail as `guard.denied` when this is raised (roadmap step
    10.6a); the Hive Entrance answers 403.
    """

    code: ClassVar[str] = "hivemind.queen.isolation_refused"

    def __init__(self, cell_id: str, reason: str) -> None:
        """Build the error for a refused isolation.

        Args:
            cell_id: The Cell the caller named.
            reason: The Guard's reason sentence.
        """
        super().__init__(f"Cell {cell_id} was not isolated: {reason}")
        self.cell_id = cell_id
