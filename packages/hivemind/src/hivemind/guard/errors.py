"""Define GuardError and the ways a Capability or a CapabilitySet can be misused on purpose.

The guard package (`hivemind.guard`) is the Hive's policy engine: `Capability` and `CapabilitySet`
say what one bee is currently allowed to do, and `access.py` says what each `AccessLevel` (a Real
Cell's READ_ONLY/SCRATCH/FULL tier, `hivemind.cell.tiers`) permits. Two things can go wrong on
purpose here: a capability string that does not parse (`InvalidCapabilityError`), and an attempt
to attenuate a `CapabilitySet` to something wider than the set doing the attenuating allows
(`CapabilityWideningError`) -- the one operation codingrules section 15 forbids outright
("Capabilities and Forage only attenuate down the tree").

`CapabilityWideningError` maps cleanly onto `hivemind.common.errors.PermissionDeniedError` ("the
caller lacks the AccessLevel or capability an operation requires"): asking `attenuate` for a
capability the attenuating set does not hold is exactly that. `InvalidCapabilityError` does not
map onto any of the six base categories -- it is not a missing resource, a state conflict, a
timeout, or a broken invariant, it is malformed caller input specific to this package's own string
grammar -- so it subclasses `GuardError` directly, the same way `hivemind.brood_chamber.errors.
InvalidGraphError` subclasses `BroodChamberError` directly for the same reason.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by `hivemind.guard.capabilities`
    (`Capability.parse`, `CapabilitySet.attenuate`) and read by every layer above that attenuates
    a capability set before handing it to a Warden or a Worker.

Key invariants:
    - Every GuardError subclass sets its own `code`; none shares a code with another.
    - CapabilityWideningError carries the offending capability's string form (not a `Capability`
      object), so this module never needs to import `hivemind.guard.capabilities` and the two
      modules stay free of an import cycle.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - .claude/codingrules.md section 15 for "Capabilities and Forage only attenuate down the tree".
    - hivemind.common.errors for HiveMindError and PermissionDeniedError, the roots this module's
      classes descend from.
    - hivemind.guard.capabilities for Capability and CapabilitySet, which raise these.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import HiveMindError, PermissionDeniedError

__all__ = ["CapabilityWideningError", "GuardError", "InvalidCapabilityError"]


class GuardError(HiveMindError):
    """Root of every error `hivemind.guard` raises on purpose.

    Subclass this for a specific failure, as `InvalidCapabilityError` does below; code that has
    nothing more specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.guard.error"


class InvalidCapabilityError(GuardError):
    """Raise when a string does not parse as a `Capability`.

    Covers an unknown family prefix, a family with no scope at all, and a scope whose grammar is
    wrong for its family (e.g. a `spend` scope that is neither `*` nor a non-negative number).
    """

    code: ClassVar[str] = "hivemind.guard.invalid_capability"

    def __init__(self, spec: str) -> None:
        """Build the error for a malformed capability string.

        Args:
            spec: The string that failed to parse, quoted verbatim in the message so the failure
                is debuggable without a stack trace.
        """
        super().__init__(f"{spec!r} is not a valid capability string.")
        self.spec = spec


class CapabilityWideningError(PermissionDeniedError):
    """Raise when `CapabilitySet.attenuate` is asked to produce a set wider than itself.

    `attenuate` is the only way a capability set is ever narrowed for a sub-bee; codingrules
    section 15 makes that direction (Queen to Warden to Worker) the only legal one, so a subset
    entry the attenuating set does not itself allow is always a caller bug, never a policy choice.
    """

    code: ClassVar[str] = "hivemind.guard.capability_widening"

    def __init__(self, offending: str) -> None:
        """Build the error for a capability the attenuating set does not allow.

        Args:
            offending: The string form of the `Capability` from the requested subset that the
                attenuating `CapabilitySet` does not allow.
        """
        super().__init__(
            f"Cannot attenuate to {offending!r}: the attenuating capability set does not allow it."
        )
        self.offending = offending
