"""Define QueenError and the ways the Queen's own bookkeeping can fail on purpose.

The Queen (`hivemind.queen.queen.Queen`, the Hive's single orchestrator and only global view) can
fail in a few ways this package raises on purpose: a `Supervisor.telemetry`/`inspect`/`intervene`
call names a Warden the Queen does not currently supervise (`UnknownWardenError`). Every subsystem
roots its own error tree at `hivemind.common.errors.HiveMindError` (codingrules section 10); this
module is `hivemind.queen`'s own root plus its specific subclasses. `PlacementError` is a sibling
of this tree but lives in `hivemind.queen.placement.decide` instead, next to the one function that
raises it, per this dispatch's own instructions.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage). Raised by
    `hivemind.queen.queen.Queen` (the `Supervisor` methods over Wardens); caught by whichever
    caller can recover. Calls into `hivemind.common.errors` only.

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

from hivemind.common.errors import HiveMindError, NotFoundError

__all__ = ["QueenError", "UnknownWardenError"]


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
