"""Define the small, individually-tested rule functions ADR-0028's ordered pipeline runs.

ADR-0028 lists six rules, hard ones first, preference last: (1) `isolation = "required"` is always
Virtual; (2) `comb_shield = NIGHT_VEIL` is always a fresh Virtual Cell; (3) a `BLOCK` Cell Wax
excludes a Cell and `allow_hive_stand = false` excludes the Hive Stand; (4) a candidate must fit --
OS, network scopes, a display or the ability to start one; (5) Forage must cover the grant; (6)
otherwise honour `prefer`, rank a `CAUTION`ed Cell behind a clean one, and prefer a dormant Cell
over a fresh provision. ADR-0031 adds one rule ahead of all six (roadmap step 10.3): a goal's
capability set is a ceiling, so a candidate the goal does not allow -- the Hive Stand without
`cell:hive_stand`, any other Real Cell without `cell:real:<cell id>`, any Virtual Cell without
`cell:virtual`, any Cell at a tier without `cell:comb_shield:<tier>` -- is excluded first,
whatever `prefer` says (`placement_needs`, `virtual_placement_needs`, `goal_lacks`). Each rule here
is one small, pure function with its own unit test (roadmap step 5.7's own words), so
`hivemind.queen.placement.decide` reads as an ordered list of named checks rather than one large
conditional. Every function takes plain data (a `TaskNeeds`, a
`CellCapabilities`, a `VirtualCellSpec`, ...) and returns a plain `bool` or ranking key; none of
them do I/O or read a store.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.placement`
    sub-package. Called only by `hivemind.queen.placement.decide.decide`. Calls into `hivemind.cell`
    (CellCapabilities, CellKind, CombShieldLevel, Isolation, TaskNeeds), `hivemind.guard`
    (Capability, CapabilityFamily, CapabilitySet), `hivemind.hive` (NetworkPolicy,
    VirtualCellSpec), `hivemind.forage` (RoleFootprint) and this package's own `policy`/`inventory`
    modules only.

Key invariants:
    - Every function here is pure: same inputs, same output, every time (ADR-0028: "`decide` is
      pure"). None of them mutate an argument or read anything beyond it.
    - Every function name says which ADR-0028 rule it implements, in its own docstring's first
      sentence, so a reader can match a rule in the ADR to the function that enforces it.
    - `goal_lacks` never excludes anything for a goal with no ceiling (None): the operator's own
      local path is limited by the Hive's other rules only.

See Also:
    - docs/adr/0028-placement-policy-real-versus-virtual.md for the six rules this module
      implements, verbatim, one function per rule.
    - hivemind.queen.placement.decide for decide, the one caller that runs these in order.
    - hivemind.queen.placement.inventory for RealCandidate and WaxMention, two of these functions'
      own argument types.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.cell import CellCapabilities, CellKind, CombShieldLevel, Isolation, TaskNeeds
from hivemind.forage import RoleFootprint
from hivemind.guard import Capability, CapabilityFamily, CapabilitySet
from hivemind.hive import NetworkPolicy, VirtualCellSpec
from hivemind.queen.placement.inventory import RealCandidate, WaxMention
from hivemind.queen.placement.policy import PlacementPolicy
from waggle.ids import CellId

# The two flag capabilities a Cell of each source asks of a goal (ADR-0031's placement families).
_CELL_VIRTUAL = Capability(family=CapabilityFamily.CELL_VIRTUAL)
_CELL_HIVE_STAND = Capability(family=CapabilityFamily.CELL_HIVE_STAND)

__all__ = [
    "caution_rank",
    "excluded_by_block_wax",
    "excluded_by_hive_stand_policy",
    "fits_exoskeleton",
    "fits_network_scopes",
    "fits_os",
    "goal_lacks",
    "isolation_requires_virtual",
    "night_veil_requires_virtual",
    "placement_needs",
    "real_has_forage",
    "tier_capability",
    "virtual_fits_exoskeleton",
    "virtual_fits_network_scopes",
    "virtual_fits_os",
    "virtual_has_forage",
    "virtual_has_headroom",
    "virtual_placement_needs",
]


def tier_capability(tier: CombShieldLevel) -> Capability:
    """The goal ceiling's tier rule: the capability a Cell at `tier` asks of a goal.

    Returns `cell:comb_shield:<tier>`, the lowercase member name (`meadow`, `propolis`,
    `night_veil`), which is the enumerated scope the Guard's grammar reads.
    """
    return Capability(family=CapabilityFamily.CELL_COMB_SHIELD, scope=tier.name.lower())


def placement_needs(candidate: RealCandidate) -> tuple[Capability, ...]:
    """The goal ceiling for an attached Cell: its source's capability, then its tier's.

    An attached Cell a Virtual backend provisioned asks `cell:virtual`, the Hive Stand
    `cell:hive_stand`, and any other Real Cell `cell:real:<cell id>`; each then asks its tier.
    Placement is one of the two callers allowed to read a Cell's kind (codingrules 8.7).
    """
    if candidate.kind is CellKind.VIRTUAL:
        cell = _CELL_VIRTUAL
    elif candidate.is_hive_stand:
        cell = _CELL_HIVE_STAND
    else:
        cell = Capability(family=CapabilityFamily.CELL_REAL, scope=candidate.cell_id)
    return (cell, tier_capability(candidate.comb_shield))


def virtual_placement_needs(tier: CombShieldLevel) -> tuple[Capability, ...]:
    """The goal ceiling for a Virtual Cell to provision or resume at `tier`: both capabilities."""
    return (_CELL_VIRTUAL, tier_capability(tier))


def goal_lacks(goal: CapabilitySet | None, needed: tuple[Capability, ...]) -> Capability | None:
    """The goal ceiling itself: the first of `needed` the goal's set does not allow, or None.

    Always None for a goal with no ceiling (`goal` is None): the operator's own local path.
    """
    if goal is None:
        return None
    return next((capability for capability in needed if not goal.allows(capability)), None)


def isolation_requires_virtual(needs: TaskNeeds) -> bool:
    """Rule 1: `isolation = "required"` always excludes every Real Cell, whatever `prefer` says."""
    return needs.isolation is Isolation.REQUIRED


def night_veil_requires_virtual(needs: TaskNeeds) -> bool:
    """Rule 2: `comb_shield = NIGHT_VEIL` is always a fresh Virtual Cell, never Real or dormant."""
    return needs.comb_shield is CombShieldLevel.NIGHT_VEIL


def excluded_by_block_wax(cell_id: CellId, blocked: Mapping[CellId, WaxMention]) -> bool:
    """Rule 3a: a Cell carrying a WRITTEN `WaxSeverity.BLOCK` note is excluded outright."""
    return cell_id in blocked


def excluded_by_hive_stand_policy(is_hive_stand: bool, policy: PlacementPolicy) -> bool:
    """Rule 3b: `[placement] allow_hive_stand = false` excludes the Hive Stand outright."""
    return is_hive_stand and not policy.allow_hive_stand


def fits_os(needs: TaskNeeds, capabilities: CellCapabilities) -> bool:
    """Rule 4a: a task's `os` need, when set, must match a Real candidate's own OS."""
    return needs.os is None or needs.os is capabilities.os


def fits_network_scopes(needs: TaskNeeds, capabilities: CellCapabilities) -> bool:
    """Rule 4b: every network scope the task needs must already be reachable from the candidate."""
    return set(needs.network_scopes) <= set(capabilities.network_scopes)


def fits_exoskeleton(needs: TaskNeeds, capabilities: CellCapabilities) -> bool:
    """Rule 4c: an Exoskeleton need requires a display, or the ability to start one."""
    if not needs.exoskeleton:
        return True
    return capabilities.has_display or capabilities.can_start_display


def real_has_forage(candidate: RealCandidate) -> bool:
    """Rule 5a: a Real Cell needs the free-capacity flag the caller already measured."""
    return candidate.has_free_capacity


def virtual_has_headroom(headroom: int | None) -> bool:
    """Rule 5b: a Virtual backend needs headroom left (`None` means it declares no limit)."""
    return headroom is None or headroom > 0


def virtual_fits_os(needs: TaskNeeds, spec: VirtualCellSpec) -> bool:
    """Rule 4a, for a Virtual spec: compare against the OS its own promised capacity reports."""
    return needs.os is None or needs.os.value == spec.capacity.host.os.value


def virtual_fits_exoskeleton(needs: TaskNeeds, spec: VirtualCellSpec) -> bool:
    """Rule 4c, for a Virtual spec: an Exoskeleton need requires a spec that provisions one."""
    return not needs.exoskeleton or spec.exoskeleton


def virtual_fits_network_scopes(needs: TaskNeeds, spec: VirtualCellSpec) -> bool:
    """Rule 4b, for a Virtual spec: a network need excludes NONE and a too-narrow allowlist."""
    if not needs.network_scopes:
        return True  # Nothing beyond the Cell's own grant is needed; every policy covers that.
    if spec.network_policy is NetworkPolicy.NONE:
        return False  # No outbound reach at all can never cover a named network scope.
    if spec.network_policy is NetworkPolicy.ALLOWLIST:
        return set(needs.network_scopes) <= set(spec.network_allowlist)
    return True  # EGRESS_ONLY/VPN_TOR: unrestricted outbound reach covers any named scope.


def virtual_has_forage(spec: VirtualCellSpec, footprint: RoleFootprint) -> bool:
    """Rule 5c: a Virtual spec's own promised capacity must cover the placed bee's footprint."""
    return (
        spec.capacity.max_sub_bees >= 1
        and spec.cpu_cores >= footprint.cpu_cores
        and spec.memory_bytes >= footprint.memory_bytes
    )


def caution_rank(cell_id: CellId, cautioned: Mapping[CellId, WaxMention]) -> int:
    """Rule 6's caution penalty: `1` (ranked behind) when CAUTIONed, `0` for a clean candidate."""
    return 1 if cell_id in cautioned else 0
