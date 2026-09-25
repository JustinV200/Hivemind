"""Refuse activating a weaker tier's egress for a task bound to a stronger one (roadmap 10.3b).

Codingrules 8.7: tier is bound to the Cell, and a task inherits the `CombShieldLevel` of the Cell
where it executes; ADR-0039's tier-inheritance floor: "a runtime path that would weaken a control
after placement is evaluated against the bound tier and denied". The bound tier travels on every
later check for the task (`PolicyContext.bound_tier`), so the slot-binding, clearance and location
floors already judge a rebind or a grant revision against it. What remains is egress itself: the
`comb_shield_egress` point activates a Cell's tier for a task, and activating a tier that ranks
below the one the task is bound to (or, before placement, the minimum tier it asked for) would
weaken its controls. That is refused under `guard.tier_floor.tier_inheritance`. Ranks follow
declaration order, MEADOW < PROPOLIS < NIGHT_VEIL.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy.floors`.
    Run by `hivemind.guard.policy.floors.chain` last. Calls into `hivemind.cell` (the tier enum),
    `hivemind.guard.capabilities`, this package's `refusal` and the policy package's `models`,
    `points` and `table`.

Key invariants:
    - Applies only at `comb_shield_egress`, to a concrete `cell:comb_shield:<tier>` need, with a
      bound tier on the context; a `*` need names no one tier and is left to the held set.
    - Refuses and never allows.

See Also:
    - .claude/codingrules.md section 8.7, "Comb Shield is a Cell property".
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Tier inheritance".
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel
from hivemind.guard.capabilities import CapabilityFamily
from hivemind.guard.policy.floors.refusal import FloorRefusal, tier
from hivemind.guard.policy.models import PolicyRequest
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.guard.policy.table import GuardPolicy

INHERITANCE_FLOOR = "tier_inheritance"  # guard.tier_floor.tier_inheritance.
_ANY_TIER = "*"  # The enumerated wildcard: every tier, so no one tier to compare.

__all__ = ["INHERITANCE_FLOOR", "inheritance_floor", "tier_rank"]


def tier_rank(level: CombShieldLevel) -> int:
    """Return a tier's rank in MEADOW < PROPOLIS < NIGHT_VEIL (declaration order).

    Args:
        level: A Comb Shield tier.

    Returns:
        0 for MEADOW, 1 for PROPOLIS, 2 for NIGHT_VEIL; a higher rank is a stronger control.
    """
    return list(CombShieldLevel).index(level)


def inheritance_floor(request: PolicyRequest, policy: GuardPolicy) -> FloorRefusal | None:
    """Refuse activating a tier's egress weaker than the tier the task is bound to.

    Args:
        request: The action: its point, needed capability and bound tier are read.
        policy: Unused: inheritance is fixed by codingrules 8.7, never configured.

    Returns:
        A `guard.tier_floor.tier_inheritance` refusal, or None when it does not apply.
    """
    del policy  # Codingrules 8.7 fixes inheritance; nothing in [guard] may loosen it.
    needed, bound = request.needed, request.context.bound_tier
    # Only egress activation, with a bound tier to compare against, can weaken a control here.
    if bound is None or request.point is not EnforcementPoint.COMB_SHIELD_EGRESS:
        return None
    if needed.family is not CapabilityFamily.CELL_COMB_SHIELD or needed.scope == _ANY_TIER:
        return None
    activating = CombShieldLevel[needed.scope.upper()]
    if tier_rank(activating) >= tier_rank(bound):
        return None
    return tier(
        INHERITANCE_FLOOR,
        f"the task is bound to {bound.name}, and {activating.name} egress would weaken its "
        "controls",
    )
