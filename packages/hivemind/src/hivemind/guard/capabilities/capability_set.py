"""Define CapabilitySet: every Capability one principal holds, and the only ways it may narrow.

A `CapabilitySet` is the frozenset of every `Capability` one bee or one enrolled device currently
holds; it is the unit every enforcement point checks an action against (`allows`). Sets flow only
downward (codingrules section 15, "Capabilities and Forage only attenuate down the tree"): the
Queen's set bounds a Warden's, a Warden's bounds each Worker's, and `attenuate` is the one method
that hands a narrower set on, raising rather than ever widening. There is deliberately no `union`
or any other method that could make a set wider; a caller that needs a set built from several
sources builds it from capabilities it already holds, as `hivemind.guard.policy.roles` does.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Built by `hivemind.guard.policy`
    (role defaults), `hivemind.guard.access` (access-level ceilings) and
    `hivemind.workers.capabilities` (a Worker's slice); read by every enforcement point and by the
    Capping gate. Calls into `hivemind.guard.capabilities.capability` and `hivemind.guard.errors`.

Key invariants:
    - Frozen and extras forbidden; `capabilities` is a frozenset, so a set is hashable and its
      order carries no meaning (`as_strings` sorts for storage and display).
    - `attenuate` returns its argument unchanged or raises `CapabilityWideningError`; it never
      returns anything this set does not already allow.
    - Every string `parse` accepts round-trips through `as_strings`.

See Also:
    - .claude/codingrules.md section 15 for the least-privilege and attenuation rules.
    - hivemind.guard.capabilities.capability for Capability and its grammar.
    - hivemind.guard.access for cap_to_access, which narrows a set to an AccessLevel.
"""

from __future__ import annotations

from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict, Field

from hivemind.guard.capabilities.capability import Capability
from hivemind.guard.errors import CapabilityWideningError

__all__ = ["CapabilitySet"]


class CapabilitySet(BaseModel):
    """Every Capability one principal holds; the unit `guard` checks an action against.

    Deliberately has no `union` or any other widening method (codingrules section 15): the only
    ways to get one are to parse it from strings, take an `attenuate`d subset of an existing one,
    or build it from capabilities already held (`hivemind.guard.policy.roles`,
    `hivemind.guard.access.cap_to_access`).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: frozenset[Capability] = Field(
        description="Every Capability this set grants; order carries no meaning."
    )

    @classmethod
    def parse(cls, *strings: str) -> CapabilitySet:
        """Build a CapabilitySet from capability strings.

        Args:
            strings: Zero or more capability strings, each parsed by `Capability.parse`.

        Returns:
            A CapabilitySet holding one Capability per distinct string.

        Raises:
            InvalidCapabilityError: Any string fails to parse; the error names it.
        """
        return cls(capabilities=frozenset(Capability.parse(spec) for spec in strings))

    @classmethod
    def empty(cls) -> CapabilitySet:
        """Build a CapabilitySet that grants nothing.

        Returns:
            A CapabilitySet whose `capabilities` is the empty frozenset.
        """
        return cls(capabilities=frozenset())

    def allows(self, needed: Capability) -> bool:
        """Decide whether some Capability in this set permits `needed`.

        Args:
            needed: The capability an action requires.

        Returns:
            True if at least one held Capability's `matches(needed)` is True.
        """
        return any(capability.matches(needed) for capability in self.capabilities)

    def attenuate(self, subset: CapabilitySet) -> CapabilitySet:
        """Narrow to `subset`, refusing to widen (codingrules section 15).

        Args:
            subset: The set a sub-bee (a Warden, a Worker) is about to receive. Every entry in it
                must already be allowed by this set.

        Returns:
            `subset`, unchanged, once every entry in it checks out.

        Raises:
            CapabilityWideningError: Some capability in `subset` is not allowed by this set; the
                error names it.
        """
        for capability in subset.capabilities:
            if not self.allows(capability):
                # Naming the offending entry shows the caller exactly which one over-reached.
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

    def as_strings(self) -> tuple[str, ...]:
        """Return every capability's string form, sorted, for stable storage and display.

        Returns:
            One string per member, in sorted order; `CapabilitySet.parse(*result)` rebuilds an
            equal set.
        """
        return tuple(sorted(str(capability) for capability in self.capabilities))

    def __len__(self) -> int:
        """Return how many Capability entries this set holds."""
        return len(self.capabilities)

    def __iter__(self) -> Iterator[Capability]:  # type: ignore[override]  # set-like on purpose
        """Iterate over this set's Capability entries in no particular order.

        Narrows pydantic's `BaseModel.__iter__` (which yields `(name, value)` field pairs) to
        yield `Capability` members instead, so a `CapabilitySet` iterates like the set it is.
        """
        return iter(self.capabilities)
