"""Define CapabilityFamily and ScopeKind: every kind of grant, and how each one's scope matches.

A capability (one thing a bee is allowed to do) is written `family` or `family:scope` (ADR-0039).
This module is the grammar's table. `CapabilityFamily` names every family the Hive knows, from a
Worker's tools to an enrolled device's Entrance routes; `ScopeKind` names the seven ways a scope
is written and matched (`net` has a kind of its own, `HOST`, because a glob or a prefix over host
names and addresses cannot express "a subdomain of" or "an address inside this network");
`FAMILIES_BY_KIND` gives each family exactly one kind, laid out like the ADR's own table so the
two can be read side by side; and the enumerated and ordered kinds carry their closed value
lists, taken from the enums they mirror (`ModelSlot`, `CombShieldLevel`, `MaskTactic`,
`HoneyClearance`). `PARSE_ORDER` is the longest-first order a string is read in, so
`tool:request` and `tool:scope:cell` are their own families rather than tools named `request` or
`scope:cell`, and `observe:honey:hive` is never read as `observe`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by `hivemind.guard.capabilities.
    scopes` (validation and matching per kind) and `.capability` (parsing). Calls into
    `hivemind.cell.tiers` (CombShieldLevel, HoneyClearance), `hivemind.forage.slots` (ModelSlot)
    and `waggle.messages.control` (MaskTactic), for the value lists only.

Key invariants:
    - Every `CapabilityFamily` member has exactly one `ScopeKind`; `_index_kinds` proves it when
      this module is imported, so a family added without a kind fails at once, not at first use.
    - A member's value is its string prefix; its name is that string in UPPER_SNAKE with ":"
      replaced by "_" (`TOOL_REQUEST = "tool:request"`).
    - Enumerated and ordered values are the lowercase member names of the enum they mirror, so a
      new `ModelSlot` is a valid `llm:` scope the moment it exists; ordered values are listed in
      rank order, lowest first.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the family table.
    - hivemind.guard.capabilities.scopes for what each ScopeKind accepts and how it matches.
    - hivemind.guard.capabilities.hosts for the HOST kind's own grammar.
    - hivemind.guard.capabilities.capability for Capability.parse, which walks PARSE_ORDER.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum
from types import MappingProxyType

from hivemind.cell.tiers import CombShieldLevel, HoneyClearance
from hivemind.forage.slots import ModelSlot
from waggle.messages.control import MaskTactic

__all__ = ["FAMILIES_BY_KIND", "PARSE_ORDER", "CapabilityFamily", "ScopeKind"]


class ScopeKind(Enum):
    """How a family's scope is written, and when a held scope satisfies a needed one (ADR-0039)."""

    FLAG = "flag"  # No scope at all: holding the family is the whole grant.
    GLOB = "glob"  # A glob over a path or a name, matched with fnmatch on its POSIX form.
    PREFIX = "prefix"  # Exact, or a held scope ending in "*" covers every scope it prefixes.
    HOST = "host"  # A host, "*", "*.<domain>", an IP literal or a CIDR network (hosts.py).
    ENUMERATED = "enumerated"  # One name from a closed list, or "*" for every name on it.
    ORDERED = "ordered"  # One rung of a ladder; a held rung covers every rung at or below it.
    AMOUNT = "amount"  # A non-negative USD amount, or "*" for no ceiling at all.


class CapabilityFamily(Enum):
    """The kind of thing one Capability grants; its value is the capability string's prefix.

    The first seven members are phase 3's (roadmap step 3.13a) and keep their values; the rest are
    ADR-0039's, added by roadmap step 10.1. `scope_kind` and `scope_values` read the tables below.
    """

    TOOL = "tool"  # Calling one named tool from a Worker's registry.
    FS_READ = "fs:read"  # Reading a path on the Cell's filesystem.
    FS_WRITE = "fs:write"  # Writing a path on the Cell's filesystem.
    NET = "net"  # Reaching a network host, a domain's subdomains or an address range.
    EXEC = "exec"  # Running a command (argv[0]) in the Cell's session.
    DEVICE = "device"  # Touching a specific device (an Exoskeleton attachment, a Swarm node).
    SPEND = "spend"  # Spending up to a USD ceiling.
    TOOL_REQUEST = "tool:request"  # Asking the Queen for a tool the Comb Registry lacks.
    TOOL_SCOPE = "tool:scope"  # Using tools the Comb Registry scopes to a Cell, not the Hive.
    CELL_VIRTUAL = "cell:virtual"  # Being placed on a Virtual Cell (a VM or container).
    CELL_HIVE_STAND = "cell:hive_stand"  # Being placed on the Hive Stand, the Queen's machine.
    CELL_REAL = "cell:real"  # Being placed on one named Real Cell (a borrowed device).
    CELL_OUTSIDE_SCRATCH = "cell:outside_scratch"  # A session call on a path outside scratch.
    CELL_COMB_SHIELD = "cell:comb_shield"  # Running at one Comb Shield tier.
    EXOSKELETON = "exoskeleton"  # Attaching the Exoskeleton (screen, input, audio) to a Cell.
    EXOSKELETON_REAL_DISPLAY = "exoskeleton:real_display"  # ... to a Real Cell's own display.
    TACTIC = "tactic"  # Invoking one Pheromone Mask tactic.
    HONEY_READ = "honey:read"  # Reading Honey (the cold knowledge tier) under a scope.
    HONEY_WRITE = "honey:write"  # Depositing ripened Honey.
    HONEY_CLEARANCE = "honey:clearance"  # The highest Honey Clearance label the holder touches.
    WAX_PROPOSE = "wax:propose"  # Proposing Cell Wax (a caution about one Cell) to the Queen.
    LLM = "llm"  # Binding one model slot.
    WARDEN_SPAWN = "warden:spawn"  # Starting a Warden (a per-Cell supervisor) on a Cell.
    FORAGE_REQUEST = "forage:request"  # Asking the Queen for more Forage (shared capacity).
    QUESTION_HUMAN = "question:human"  # Routing a question up to the human.
    OBSERVE = "observe"  # Reading the Observation Hive's fleet, task and trail views.
    OBSERVE_THOUGHTS = "observe:thoughts"  # Reading bees' episode records (their thoughts).
    OBSERVE_HONEY = "observe:honey"  # Browsing Honey in the Observation Hive under a scope.
    ENTRANCE_SUBMIT = "entrance:submit"  # Submitting a goal through the Hive Entrance.
    ENTRANCE_ANSWER = "entrance:answer"  # Answering the human's inbox through the Entrance.
    ENTRANCE_PUSH = "entrance:push"  # Receiving push notifications from the Entrance.
    ENTRANCE_STEWARD = "entrance:steward"  # Approving other devices remotely, after step-up.
    SUPERSEDE = "supersede"  # Moving the Hive Stand to another machine (Supersedure).
    STING_CUT = "sting_cut"  # Cutting one Cell off in an emergency (Sting Cut).
    GEO = "geo"  # Reading the device's location under a scope.
    WIFI_SCAN = "wifi:scan"  # Scanning the Wi-Fi networks around the device.
    HOST_METADATA = "host:metadata"  # Reading the host's identifying metadata.
    WATCH = "watch"  # Watch mode on one node: read-only observation (phase 11.10).

    @property
    def scope_kind(self) -> ScopeKind:
        """Return how this family's scope is written and matched.

        Returns:
            The one ScopeKind `FAMILIES_BY_KIND` gives this family.
        """
        return _KIND_OF[self]

    @property
    def scope_values(self) -> tuple[str, ...]:
        """Return the closed list of scopes an enumerated or ordered family accepts.

        Returns:
            The lowercase names this family's scope may take (rank order, lowest first, for an
            ordered family); empty for every other kind, whose scopes are open text.
        """
        return _SCOPE_VALUES.get(self, ())


# ADR-0039's table, one entry per ScopeKind, so this and the ADR can be compared row by row.
FAMILIES_BY_KIND: Mapping[ScopeKind, frozenset[CapabilityFamily]] = MappingProxyType(
    {
        ScopeKind.FLAG: frozenset(
            {
                CapabilityFamily.TOOL_REQUEST,
                CapabilityFamily.CELL_VIRTUAL,
                CapabilityFamily.CELL_HIVE_STAND,
                CapabilityFamily.EXOSKELETON,
                CapabilityFamily.EXOSKELETON_REAL_DISPLAY,
                CapabilityFamily.HONEY_WRITE,
                CapabilityFamily.WAX_PROPOSE,
                CapabilityFamily.WARDEN_SPAWN,
                CapabilityFamily.FORAGE_REQUEST,
                CapabilityFamily.QUESTION_HUMAN,
                CapabilityFamily.OBSERVE,
                CapabilityFamily.OBSERVE_THOUGHTS,
                CapabilityFamily.ENTRANCE_SUBMIT,
                CapabilityFamily.ENTRANCE_ANSWER,
                CapabilityFamily.ENTRANCE_PUSH,
                CapabilityFamily.ENTRANCE_STEWARD,
                CapabilityFamily.SUPERSEDE,
                CapabilityFamily.STING_CUT,
                CapabilityFamily.WIFI_SCAN,
                CapabilityFamily.HOST_METADATA,
            }
        ),
        ScopeKind.GLOB: frozenset(
            {
                CapabilityFamily.TOOL,
                CapabilityFamily.FS_READ,
                CapabilityFamily.FS_WRITE,
                CapabilityFamily.EXEC,
                CapabilityFamily.CELL_OUTSIDE_SCRATCH,
            }
        ),
        ScopeKind.PREFIX: frozenset(
            {
                CapabilityFamily.DEVICE,
                CapabilityFamily.CELL_REAL,
                CapabilityFamily.HONEY_READ,
                CapabilityFamily.OBSERVE_HONEY,
                CapabilityFamily.GEO,
                CapabilityFamily.WATCH,
                CapabilityFamily.TOOL_SCOPE,
            }
        ),
        ScopeKind.HOST: frozenset({CapabilityFamily.NET}),
        ScopeKind.ENUMERATED: frozenset(
            {CapabilityFamily.LLM, CapabilityFamily.CELL_COMB_SHIELD, CapabilityFamily.TACTIC}
        ),
        ScopeKind.ORDERED: frozenset({CapabilityFamily.HONEY_CLEARANCE}),
        ScopeKind.AMOUNT: frozenset({CapabilityFamily.SPEND}),
    }
)

# Longest family string first: the order Capability.parse tries families in (ADR-0039). sorted()
# is stable, so families of equal length keep declaration order; none of them can both match one
# string anyway, since two different prefixes of the same length never prefix the same text.
PARSE_ORDER: tuple[CapabilityFamily, ...] = tuple(
    sorted(CapabilityFamily, key=lambda family: len(family.value), reverse=True)
)


def _index_kinds(
    by_kind: Mapping[ScopeKind, frozenset[CapabilityFamily]],
) -> Mapping[CapabilityFamily, ScopeKind]:
    """Invert FAMILIES_BY_KIND, asserting every family appears under exactly one kind.

    Args:
        by_kind: The per-kind family table to invert.

    Returns:
        An immutable family-to-kind mapping with one entry per CapabilityFamily member.
    """
    kinds: dict[CapabilityFamily, ScopeKind] = {}
    for kind, families in by_kind.items():
        for family in families:
            # A family under two kinds would match two ways; that is a bug in the table above,
            # so it fails at import (pure computation, codingrules 5.5), not at first use.
            if family in kinds:
                raise AssertionError(f"{family.value!r} is listed as {kinds[family]} and {kind}.")
            kinds[family] = kind
    missing = [family.value for family in CapabilityFamily if family not in kinds]
    if missing:
        raise AssertionError(f"capability families with no ScopeKind: {missing}.")
    return MappingProxyType(kinds)


def _lowercase_names(members: Iterable[Enum]) -> tuple[str, ...]:
    """Return each enum member's name, lowercased, in the order given."""
    return tuple(member.name.lower() for member in members)


_KIND_OF = _index_kinds(FAMILIES_BY_KIND)

# HoneyClearance in rank order, lowest first: the ladder the one ordered family's ranks read.
_CLEARANCE_LADDER: list[HoneyClearance] = sorted(HoneyClearance, key=lambda level: level.rank)

# The closed scope lists: each enumerated family mirrors its enum's members, and the ordered one
# mirrors the clearance ladder above.
_SCOPE_VALUES: Mapping[CapabilityFamily, tuple[str, ...]] = MappingProxyType(
    {
        CapabilityFamily.LLM: _lowercase_names(ModelSlot),
        CapabilityFamily.CELL_COMB_SHIELD: _lowercase_names(CombShieldLevel),
        CapabilityFamily.TACTIC: _lowercase_names(MaskTactic),
        CapabilityFamily.HONEY_CLEARANCE: _lowercase_names(_CLEARANCE_LADDER),
    }
)
