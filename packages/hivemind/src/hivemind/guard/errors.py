"""Define GuardError and the ways the capability grammar or the Guard policy can be misused.

The guard package (`hivemind.guard`) is the Hive's policy engine: `Capability` and `CapabilitySet`
say what one principal is allowed to do, `access.py` says what each `AccessLevel` (a Real Cell's
READ_ONLY/SCRATCH/FULL tier, `hivemind.cell.tiers`) permits, and `policy/` decides one action at a
time. Three things can go wrong on purpose here: a capability string that does not parse
(`InvalidCapabilityError`), an attempt to attenuate a `CapabilitySet` to something wider than the
set doing the attenuating allows (`CapabilityWideningError`) -- the one operation codingrules
section 15 forbids outright ("Capabilities and Forage only attenuate down the tree") -- and a
Guard policy (the shipped TOML, an operator's `policy_file`, or the manifest's `[guard]` table)
that names an unknown role, point or action, or carries an entry that is not a capability
(`GuardPolicyError`). Roadmap step 10.3a adds a fourth, for the one network question the Guard
asks before a tool connects: a destination host that does not resolve to any address
(`UnresolvableHostError`), which leaves nothing checked to connect to.

`CapabilityWideningError` maps cleanly onto `hivemind.common.errors.PermissionDeniedError` ("the
caller lacks the AccessLevel or capability an operation requires"): asking `attenuate` for a
capability the attenuating set does not hold is exactly that. `InvalidCapabilityError` maps onto
none of the base categories: it is malformed input in this package's own string grammar. And
`GuardPolicyError`, though a bad `[guard]` table is a configuration problem, is also what a caller
gets for asking a valid policy for a role it does not define, which is not one; so both subclass
`GuardError` directly, the same way `hivemind.brood_chamber.errors.InvalidGraphError` subclasses
`BroodChamberError` directly.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Raised by `hivemind.guard.capabilities`
    (`Capability.parse`, `CapabilitySet.attenuate`) and `hivemind.guard.policy` (loading a policy,
    asking it for a role); read by every layer above that attenuates a capability set or builds a
    policy in its composition root.

Key invariants:
    - Every GuardError subclass sets its own `code`; none shares a code with another.
    - Errors carry strings (the offending capability's string form, the offending entry), never a
      `Capability` object, so this module imports nothing else from `hivemind.guard` and stays
      free of an import cycle.

See Also:
    - .claude/codingrules.md section 10 for the exceptions and errors rules this module follows.
    - .claude/codingrules.md section 15 for "Capabilities and Forage only attenuate down the tree".
    - hivemind.common.errors for HiveMindError and PermissionDeniedError, the roots this module's
      classes descend from.
    - hivemind.guard.capabilities and hivemind.guard.policy, which raise these.
"""

from __future__ import annotations

from typing import ClassVar

from hivemind.common.errors import HiveMindError, PermissionDeniedError

__all__ = [
    "CapabilityWideningError",
    "GuardError",
    "GuardPolicyError",
    "InvalidCapabilityError",
    "UnresolvableHostError",
]


class GuardError(HiveMindError):
    """Root of every error `hivemind.guard` raises on purpose.

    Subclass this for a specific failure, as `InvalidCapabilityError` does below; code that has
    nothing more specific to say may raise this directly.
    """

    code: ClassVar[str] = "hivemind.guard.error"


class InvalidCapabilityError(GuardError):
    """Raise when a string does not parse as a `Capability`.

    Covers an unknown family, a flag family given a scope (`observe:foo`), a scoped family with
    no scope (`fs:read`, `tool:scope:`), and a scope its family's kind refuses (a `spend` that is
    neither `*` nor a non-negative amount, a `net` wildcard that is not `*` or `*.domain`).
    """

    code: ClassVar[str] = "hivemind.guard.invalid_capability"

    def __init__(self, spec: str, reason: str = "") -> None:
        """Build the error for a malformed capability string.

        Args:
            spec: The string that failed to parse, quoted verbatim in the message so the failure
                is debuggable without a stack trace.
            reason: Why the grammar refused it, when known (`"llm:nonsense"` names the slots it
                could have been); empty when there is nothing more specific to say.
        """
        detail = f": {reason}" if reason else ""
        super().__init__(f"{spec!r} is not a valid capability string{detail}.")
        self.spec = spec
        self.reason = reason


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


class GuardPolicyError(GuardError):
    """Raise when a Guard policy cannot be read, or names something the policy engine lacks.

    Covers an unreadable or invalid policy TOML, a role, enforcement point or escalation action
    that does not exist, an entry that is not a capability (named in the message), a `proposed`
    list anywhere but the `device` role or wider than its `allow`, and a request for a role set
    the policy cannot build. Raised when a composition root builds the policy, so a bad
    `[guard]` table stops the Hive at start instead of surfacing at the first enforcement point.
    """

    code: ClassVar[str] = "hivemind.guard.invalid_policy"


class UnresolvableHostError(GuardError):
    """Raise when a destination host does not resolve to any address a connection could use.

    Raised by `hivemind.guard.net.resolve.resolve_host` for a name the resolver does not know, an
    answer with no address in it, or a lookup that did not finish in time. The caller refuses to
    connect: with no address checked, there is nothing the Guard has allowed.
    """

    code: ClassVar[str] = "hivemind.guard.unresolvable_host"

    def __init__(self, host: str, reason: str) -> None:
        """Build the error for a host that resolved to nothing usable.

        Args:
            host: The host that did not resolve, quoted in the message.
            reason: What went wrong, briefly: the resolver's error class, or "no address".
        """
        super().__init__(f"Host {host!r} did not resolve to an address ({reason}).")
        self.host = host
        self.reason = reason
