"""The capability grammar: every family, how each scope matches, one grant and a set of them.

A capability is one thing a principal (the operator, the Queen, a Warden, a Worker, a Swarm
device or an enrolled client device) is allowed to do, written `family` or `family:scope`
(ADR-0039). This package holds the grammar in five modules: `families` (every
`CapabilityFamily`, its `ScopeKind` and the longest-first parse order), `scopes` (what each kind
accepts and how a held scope covers a needed one), `hosts` (the `net` family's own host grammar),
`capability` (`Capability`, one grant) and `capability_set` (`CapabilitySet`, everything one
principal holds). It grew out of phase 3's single `guard/capabilities.py` (roadmap 3.13a) when
roadmap step 10.1 added the rest of the families; every name that module exported is still
importable from here and from `hivemind.guard`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Read by `hivemind.guard.access` and
    `hivemind.guard.policy`, and by every layer above that states what an action needs or holds.
    Calls into `hivemind.cell.tiers`, `hivemind.forage.slots` and `waggle.messages.control` for
    the enumerated value lists only.

Key invariants:
    - Every string a `Capability` or `CapabilitySet` accepts round-trips exactly.
    - Nothing in this package widens a set: `CapabilitySet` has no union, and `attenuate` raises
      rather than returning anything its own set does not allow.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the decision.
    - hivemind.guard.access and hivemind.guard.policy for the two consumers inside guard.

Public API:
    - CapabilityFamily, ScopeKind, FAMILIES_BY_KIND, PARSE_ORDER: the family table (families).
    - Capability: one grant, with `parse`, `str()` and `matches` (capability).
    - CapabilitySet: every grant one principal holds, with `parse`, `empty`, `allows`,
      `attenuate`, `issubset`, `as_strings` (capability_set).
    - glob_literal: escape a path before embedding it in a glob scope (scopes).
"""

from hivemind.guard.capabilities.capability import Capability
from hivemind.guard.capabilities.capability_set import CapabilitySet
from hivemind.guard.capabilities.families import (
    FAMILIES_BY_KIND,
    PARSE_ORDER,
    CapabilityFamily,
    ScopeKind,
)
from hivemind.guard.capabilities.scopes import glob_literal

__all__ = [
    "FAMILIES_BY_KIND",
    "PARSE_ORDER",
    "Capability",
    "CapabilityFamily",
    "CapabilitySet",
    "ScopeKind",
    "glob_literal",
]
