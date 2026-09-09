"""Define Capability, CapabilityFamily and CapabilitySet: what one bee is currently allowed to do.

A `Capability` is one grant in one family -- a tool it may call, a path it may read or write, a
network scope it may reach, a command it may execute, a device it may touch, or a spend ceiling in
USD -- written as a single string, `"<family>:<scope>"` (`"fs:write:/scratch/**"`,
`"net:api.example.com"`, `"spend:5.00"`). A `CapabilitySet` is the frozenset of every `Capability`
one bee currently holds. Phase 3 step 3.13a builds the pure core -- the families a Worker's tools
and a Cell's access level already need -- and phase 10 step 10.1 extends the family list with the
Entrance, Honey and Exoskeleton scopes once those subsystems exist; only the families this module
declares are legal until then.

Scope grammar, by family (`CapabilityFamily`):
    - `tool`, `fs:read`, `fs:write`, `exec`: a glob pattern. Matched with `fnmatch.fnmatchcase`
      against a POSIX-style (forward-slash) form of the scope, on both sides, so a scope written
      with Windows backslashes still matches a POSIX-style one and vice versa. `fnmatch`'s `*`
      already matches across `/`, so `**` behaves exactly like `*` here; it is written that way in
      examples because it reads better over a directory tree (`"fs:write:/scratch/**"`).
    - `net`, `device`: an exact string, or a scope ending in `*` that matches any needed scope
      sharing its prefix (`"net:*.example.com"` is written without the trailing `*` convention
      used elsewhere in this grammar -- see `Capability.matches` for the precise rule).
    - `spend`: a non-negative decimal amount in USD, or `*` for no ceiling. A held `spend`
      capability matches a needed one when the held amount is `>=` the needed amount.

A family with no scope at all (`"fs:read"` with nothing after it) is never valid: every family
requires a scope, even if that scope is the wildcard `*`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by every layer above it before
    an action is allowed to proceed: `hivemind.guard.access` builds a `CapabilitySet` ceiling from
    an `AccessLevel`, and `hivemind.workers.capabilities` (phase 3 step 3.15) attenuates a
    Warden's set down to what one Worker needs. Calls into `hivemind.common` only; this module is
    pure (no I/O, per this step's brief).

Key invariants:
    - `Capability` and `CapabilitySet` are frozen and forbid unknown keys, like every boundary
      value in this repository; both are hashable, since `CapabilitySet.capabilities` is itself a
      `frozenset`.
    - `str(Capability.parse(s)) == s` for every string this module accepts, and
      `Capability.parse(str(capability)) == capability` for every `Capability` this module builds.
    - `CapabilitySet.attenuate` never returns anything wider than its own set: it either returns
      `subset` unchanged or raises `CapabilityWideningError` (codingrules section 15,
      "Capabilities and Forage only attenuate down the tree"). There is deliberately no `union`
      method; nothing in this package can make a `CapabilitySet` wider.

See Also:
    - .claude/codingrules.md section 15 for the least-privilege and attenuation rules this module
      enforces.
    - .claude/roadmap.md phase 3 step 3.13a for this step's scope, and phase 10 step 10.1 for the
      families this module leaves for later.
    - hivemind.guard.access for `ceiling_for` and `cap_to_access`, which build and narrow
      `CapabilitySet`s from an `AccessLevel`.
    - hivemind.guard.errors for `InvalidCapabilityError` and `CapabilityWideningError`.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.guard.errors import CapabilityWideningError, InvalidCapabilityError

__all__ = ["Capability", "CapabilityFamily", "CapabilitySet"]

_SPEND_WILDCARD = "*"  # A spend scope of "*" means no ceiling: every needed amount satisfies it.


class CapabilityFamily(Enum):
    """The kind of thing one Capability grants; also its string prefix up to (not including) ":".

    Phase 3 step 3.13a's set; phase 10 step 10.1 adds the Entrance, Honey and Exoskeleton
    families once those subsystems exist (roadmap phase 3 step 3.13a note).
    """

    TOOL = "tool"  # Calling a named tool a Worker's registry offers.
    FS_READ = "fs:read"  # Reading a path on the Cell's filesystem.
    FS_WRITE = "fs:write"  # Writing a path on the Cell's filesystem.
    NET = "net"  # Reaching a network host or scope.
    EXEC = "exec"  # Running a command (argv[0]) in the Cell's session.
    DEVICE = "device"  # Touching a specific device (Exoskeleton attachment, Swarm node, ...).
    SPEND = "spend"  # Spending up to a USD ceiling.


class Capability(BaseModel):
    """One grant in one family: a family plus a scope string, together written "family:scope".

    Constructed either directly (`Capability(family=CapabilityFamily.NET, scope="*")`) or, more
    commonly, through `Capability.parse` from the wire/manifest string form. See the module
    docstring for the scope grammar each family expects.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    family: CapabilityFamily = Field(description="Which kind of grant this is.")
    scope: str = Field(
        min_length=1,
        description="Everything after the family prefix and its colon; never empty.",
    )

    @classmethod
    def parse(cls, spec: str) -> Capability:
        """Parse a `"family:scope"` string into a Capability.

        Args:
            spec: The full capability string, e.g. `"fs:write:/scratch/**"`. The scope is
                everything after the family's own prefix (`"fs:write"`, not `"fs"`), so a scope
                may itself contain colons (a Windows path's drive letter, for instance).

        Returns:
            The parsed Capability.

        Raises:
            InvalidCapabilityError: No known family prefixes `spec`, the scope after the prefix
                is empty, or the scope's grammar is wrong for its family (a non-numeric,
                non-`*` `spend` scope).
        """
        for family in CapabilityFamily:
            prefix = f"{family.value}:"
            if spec.startswith(prefix):
                scope = spec[len(prefix) :]
                if not scope:
                    raise InvalidCapabilityError(spec)
                _validate_scope(family, scope, spec)
                return cls(family=family, scope=scope)
        # No family prefix matched at all: an unknown family, or a bare family with no colon.
        raise InvalidCapabilityError(spec)

    def __str__(self) -> str:
        """Render back to the `"family:scope"` string `parse` accepts.

        Returns:
            The round-trip string form, e.g. `"fs:write:/scratch/**"`.
        """
        return f"{self.family.value}:{self.scope}"

    def matches(self, needed: Capability) -> bool:
        """Decide whether this (held) Capability satisfies a needed one.

        Args:
            needed: The capability an action requires. Must be in the same family to have any
                chance of matching; scopes are compared per the family-specific grammar in the
                module docstring.

        Returns:
            True if holding this Capability is enough to permit `needed`.
        """
        if self.family != needed.family:
            return False  # A Capability never satisfies a need in a different family.
        if self.family is CapabilityFamily.SPEND:
            # A spend ceiling is satisfied when what is held covers what is needed.
            return _spend_amount(self.scope) >= _spend_amount(needed.scope)
        if self.family in (CapabilityFamily.NET, CapabilityFamily.DEVICE):
            # Exact match, or this scope ends in a wildcard that covers the needed scope's prefix.
            return self.scope == needed.scope or (
                self.scope.endswith("*") and needed.scope.startswith(self.scope[:-1])
            )
        # Remaining families (tool, fs:read, fs:write, exec) are glob patterns over POSIX-style
        # paths/names; fnmatch's "*" already matches across "/", so no separate "**" handling.
        return fnmatch.fnmatchcase(_to_posix(needed.scope), _to_posix(self.scope))


class CapabilitySet(BaseModel):
    """Every Capability one bee currently holds; the unit `guard` checks an action against.

    Deliberately has no `union` or any other widening method (codingrules section 15): the only
    way to get a `CapabilitySet` is to build one from strings, take an `attenuate`d subset of an
    existing one, or narrow one against an `AccessLevel` ceiling (`hivemind.guard.access.
    cap_to_access`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: frozenset[Capability] = Field(
        description="Every Capability this set grants; order carries no meaning."
    )

    @classmethod
    def parse(cls, *strings: str) -> CapabilitySet:
        """Build a CapabilitySet from `"family:scope"` strings.

        Args:
            strings: Zero or more capability strings, each parsed by `Capability.parse`.

        Returns:
            A CapabilitySet holding one Capability per string.

        Raises:
            InvalidCapabilityError: Any string fails to parse.
        """
        return cls(capabilities=frozenset(Capability.parse(s) for s in strings))

    @classmethod
    def empty(cls) -> CapabilitySet:
        """Build a CapabilitySet that grants nothing.

        Returns:
            A CapabilitySet whose `capabilities` is the empty frozenset.
        """
        return cls(capabilities=frozenset())

    def allows(self, needed: Capability) -> bool:
        """Decide whether some Capability in this set satisfies `needed`.

        Args:
            needed: The capability an action requires.

        Returns:
            True if at least one held Capability's `matches(needed)` is True.
        """
        return any(capability.matches(needed) for capability in self.capabilities)

    def attenuate(self, subset: CapabilitySet) -> CapabilitySet:
        """Narrow to `subset`, refusing to widen (codingrules section 15).

        Args:
            subset: The capability set a sub-bee (a Warden, a Worker) is about to receive. Every
                entry in it must already be allowed by this set.

        Returns:
            `subset`, unchanged, once every entry in it checks out.

        Raises:
            CapabilityWideningError: Some capability in `subset` is not allowed by this set.
        """
        for capability in subset.capabilities:
            if not self.allows(capability):
                # Naming the offending capability so the caller can see which entry over-reached.
                raise CapabilityWideningError(str(capability))
        return subset

    def issubset(self, other: CapabilitySet) -> bool:
        """Decide whether everything this set grants is also granted by `other`.

        Args:
            other: The set to check coverage against.

        Returns:
            True if `other.allows(capability)` holds for every Capability in this set.
        """
        return all(other.allows(capability) for capability in self.capabilities)

    def __len__(self) -> int:
        """Return how many Capability entries this set holds."""
        return len(self.capabilities)

    def __iter__(self) -> Iterator[Capability]:  # type: ignore[override]  # set-like on purpose
        """Iterate over this set's Capability entries in no particular order.

        Narrows pydantic's `BaseModel.__iter__` (which yields `(name, value)` field pairs) to
        yield `Capability` members instead, so a `CapabilitySet` iterates like the set it is.
        """
        return iter(self.capabilities)


def _to_posix(scope: str) -> str:
    """Normalise a scope's separators to forward slashes so Windows and POSIX paths compare equal.

    Args:
        scope: A `fs:read`, `fs:write`, `tool` or `exec` scope, in whatever separator style the
            caller wrote it.

    Returns:
        The same scope with every backslash replaced by a forward slash.
    """
    return scope.replace("\\", "/")


def _spend_amount(scope: str) -> float:
    """Parse a `spend` scope into a comparable amount, treating `*` as unlimited.

    Args:
        scope: A `spend` capability's scope: `*`, or a non-negative decimal string.

    Returns:
        `math.inf` for `*`; otherwise the parsed amount.
    """
    if scope == _SPEND_WILDCARD:
        return float("inf")
    return float(scope)  # _validate_scope already proved this parses and is non-negative.


def _validate_scope(family: CapabilityFamily, scope: str, spec: str) -> None:
    """Reject a scope whose grammar is wrong for its family, before a Capability is built.

    Args:
        family: The family `scope` was parsed under.
        scope: The candidate scope, already known to be non-empty.
        spec: The original full string, for the error message.

    Raises:
        InvalidCapabilityError: `family` is SPEND and `scope` is neither `*` nor a non-negative
            number.
    """
    # Only `spend` has a grammar narrower than "any non-empty string"; every other family's scope
    # is a glob, an exact-or-wildcard string, or a device id, all of which accept any text.
    if family is not CapabilityFamily.SPEND or scope == _SPEND_WILDCARD:
        return
    try:
        amount = float(scope)
    except ValueError as exc:
        raise InvalidCapabilityError(spec) from exc
    if amount < 0:
        raise InvalidCapabilityError(spec)
