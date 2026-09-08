"""Define AccessLevel, CombShieldLevel and HoneyClearance: a Cell's three security dimensions.

A Cell is a machine, Real (an existing device the Hive borrows and leaves exactly as found) or
Virtual (a VM or container the Hive provisions and later destroys), that a Worker (a subagent
performing one role) runs on. Three separate dimensions describe what the Hive may do with one:
``AccessLevel`` (how much of a Real Cell the Hive may touch; a Virtual Cell is always FULL),
``CombShieldLevel`` (a Cell's security tier: what network egress a task on it must run through)
and ``HoneyClearance`` (the data-sensitivity label carried by every memory tier, not only Honey,
the Hive's cold knowledge store). They never collapse into one score: a FULL-access Cell can still
be MEADOW tier, and a NIGHT_VEIL Cell still only ever touches C0/C1 data. This module mirrors
``waggle.messages.labels``'s enums of the same names, member for member, because the wire carries
the same three dimensions on task, Cell and Forage messages; a sync test in
``tests/unit/cell/test_tiers.py`` keeps them from drifting apart.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by guard (the policy engine,
    Layer 2, to decide what a capability set may grant), hive and swarm (Layer 3, which produce
    Cells at a given tier) and supervision (Capping, which checks a proposal's reach against a
    Cell's tier). Calls into waggle.messages only, for the from_wire/to_wire conversions.

Key invariants:
    - Member names and values are identical to waggle.messages.labels's AccessLevel,
      CombShieldLevel and HoneyClearance (tests/unit/cell/test_tiers.py checks it member for
      member).
    - AccessLevel and HoneyClearance are totally ordered by declaration; rank is the only thing a
      validator compares, never the member or its wire value.
    - A Real Cell is never CombShieldLevel.NIGHT_VEIL (codingrules section 8.7); this module holds
      the enum only, the constraint is enforced where a Real Cell's tier is set (swarm, cell.local).

See Also:
    - .claude/codingrules.md section 8.7 for what each CombShieldLevel tier requires operationally.
    - .claude/codingrules.md section 6.1 for the AccessLevel / CombShieldLevel / HoneyClearance
      table this module implements.
    - waggle.messages.labels for the wire form these enums mirror.
    - hivemind.cell.needs for TaskNeeds.comb_shield, the field that carries this tier per task.
"""

from __future__ import annotations

from enum import Enum

from waggle.messages import AccessLevel as WireAccessLevel
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages import HoneyClearance as WireHoneyClearance

__all__ = ["AccessLevel", "CombShieldLevel", "HoneyClearance"]


class AccessLevel(Enum):
    """How much of a Real Cell the Hive may touch; a Virtual Cell is always FULL.

    Set by the operator when a Real Cell (the Hive Stand or a Swarm device) joins, and stored
    with the node and every lease taken on it. Totally ordered READ_ONLY < SCRATCH < FULL by
    declaration; compare `rank`, never the values. Caps every capability set issued for that Cell,
    however the policy in `guard` is configured.
    """

    READ_ONLY = "READ_ONLY"  # Observe only; nothing is ever written anywhere on the Cell.
    SCRATCH = "SCRATCH"  # Writes stay inside the lease's own scratch directory.
    FULL = "FULL"  # The whole Cell is reachable, within the Cell's other controls.

    @property
    def rank(self) -> int:
        """Position in the total order READ_ONLY < SCRATCH < FULL.

        Returns:
            0 for READ_ONLY, 1 for SCRATCH, 2 for FULL; a higher rank permits more.
        """
        # Declaration order is the total order, exactly as waggle.messages.AccessLevel.rank
        # defines it; comparing ranks keeps the wire strings free to read well on their own.
        return list(AccessLevel).index(self)

    @classmethod
    def from_wire(cls, wire: WireAccessLevel) -> AccessLevel:
        """Convert the wire form of this tier into hivemind's own enum.

        Args:
            wire: The waggle.messages.AccessLevel value read off an Envelope.

        Returns:
            The hivemind AccessLevel member with the same name.
        """
        # Values are identical strings on both sides (the sync test enforces it), so a plain
        # value lookup is the whole conversion.
        return cls(wire.value)

    def to_wire(self) -> WireAccessLevel:
        """Convert this tier into the wire form waggle.messages carries on an Envelope.

        Returns:
            The waggle.messages.AccessLevel member with the same name.
        """
        return WireAccessLevel(self.value)


class CombShieldLevel(Enum):
    """A Cell's security tier: what network egress a task on that Cell must run through.

    Bound to the Cell, not the task (codingrules section 8.7): a task inherits the
    CombShieldLevel of the Cell where it runs. Operator-set at enrolment for a Real Cell;
    Queen-chosen at provisioning for a Virtual one, which alone may reach NIGHT_VEIL.
    """

    MEADOW = "MEADOW"  # Tier 0: default baseline, any machine, no VPN required.
    PROPOLIS = "PROPOLIS"  # Tier 1: hardened baseline, OpenVPN required, Tor disabled.
    NIGHT_VEIL = "NIGHT_VEIL"  # Tier 2: virtual-only, OpenVPN plus Tor, teardown-only.

    @classmethod
    def from_wire(cls, wire: WireCombShieldLevel) -> CombShieldLevel:
        """Convert the wire form of this tier into hivemind's own enum.

        Args:
            wire: The waggle.messages.CombShieldLevel value read off an Envelope.

        Returns:
            The hivemind CombShieldLevel member with the same name.
        """
        return cls(wire.value)

    def to_wire(self) -> WireCombShieldLevel:
        """Convert this tier into the wire form waggle.messages carries on an Envelope.

        Returns:
            The waggle.messages.CombShieldLevel member with the same name.
        """
        return WireCombShieldLevel(self.value)


class HoneyClearance(Enum):
    """A data-sensitivity label carried by every memory tier, not only Honey (the cold tier).

    Totally ordered C0 < C1 < C2 by declaration; compare `rank`, never the values. Any detail
    about the operator or a Real Cell -- a first name or a habit included -- is C2. Labels come
    from provenance at intake; a model may raise a label, only a judge verdict or a human may
    lower one.
    """

    C0 = "C0"  # Wildflower: public, non-sensitive.
    C1 = "C1"  # Apiary: internal, non-personal.
    C2 = "C2"  # Royal: personal or sensitive; anything describing the operator or a Real Cell.

    @property
    def rank(self) -> int:
        """Position in the total order C0 < C1 < C2.

        Returns:
            0 for C0, 1 for C1, 2 for C2; a higher rank is more sensitive.
        """
        return list(HoneyClearance).index(self)

    @classmethod
    def from_wire(cls, wire: WireHoneyClearance) -> HoneyClearance:
        """Convert the wire form of this label into hivemind's own enum.

        Args:
            wire: The waggle.messages.HoneyClearance value read off an Envelope.

        Returns:
            The hivemind HoneyClearance member with the same name.
        """
        return cls(wire.value)

    def to_wire(self) -> WireHoneyClearance:
        """Convert this label into the wire form waggle.messages carries on an Envelope.

        Returns:
            The waggle.messages.HoneyClearance member with the same name.
        """
        return WireHoneyClearance(self.value)
