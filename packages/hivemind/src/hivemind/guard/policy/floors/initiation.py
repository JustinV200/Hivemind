"""Refuse Night Veil work nobody but a human asked for: the initiation floor (roadmap step 10.3c).

ADR-0039, "Night Veil is initiated only by a human, structurally": only a goal request that asked
for `comb_shield = NIGHT_VEIL` explicitly, submitted by a human (an enrolled device holding
`cell:comb_shield:night_veil`, or the operator's own CLI), may produce Night Veil work. A chat
message never initiates it, because a model reading possibly injected text would then be the
initiator; a planner that sets the tier on a task of its own accord is exactly such a model; and
neither the Queen nor a Warden may escalate to it. So when Night Veil work is placed, or its Cell's
egress activated, this floor requires three facts on the request's context: the task's own origin
is HUMAN, the task cites a goal request (`PolicyContext.goal_request`, read by the Queen from the
request's stored row), and that row says origin HUMAN and tier NIGHT_VEIL. Anything less is
refused under `guard.tier_floor.night_veil_initiation`, naming which fact was missing.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy.floors`.
    Run by `hivemind.guard.policy.floors.chain`. Calls into `hivemind.cell` (the tier enum and
    RequestOrigin), this package's `night_veil` and `refusal`, and the policy package's `models`,
    `points` and `table`.

Key invariants:
    - Applies only at the points where Night Veil work begins: placement and Comb Shield egress
      activation. Every later check for a placed task relies on the placement having passed here.
    - Refuses and never allows: a request that passes still needs `cell:virtual` and
      `cell:comb_shield:night_veil` held (the goal's set, when it carries one).

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Night Veil is
      initiated only by a human, structurally".
    - hivemind.queen.dispatcher for the placement point that states the goal request's facts.
"""

from __future__ import annotations

from hivemind.cell import CombShieldLevel, RequestOrigin
from hivemind.guard.policy.floors.night_veil import is_night_veil
from hivemind.guard.policy.floors.refusal import FloorRefusal, tier
from hivemind.guard.policy.models import PolicyContext, PolicyRequest
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.guard.policy.table import GuardPolicy

INITIATION_FLOOR = "night_veil_initiation"  # guard.tier_floor.night_veil_initiation.
# Where Night Veil work begins: choosing its Cell, and switching that Cell's egress on.
INITIATION_POINTS = frozenset({EnforcementPoint.PLACEMENT, EnforcementPoint.COMB_SHIELD_EGRESS})

__all__ = ["INITIATION_FLOOR", "INITIATION_POINTS", "initiation_floor"]


def initiation_floor(request: PolicyRequest, policy: GuardPolicy) -> FloorRefusal | None:
    """Refuse Night Veil placement or egress unless a human's own request named the tier.

    Args:
        request: The action: its point and context (origin, goal request facts) are read.
        policy: Unused: who may initiate Night Veil is fixed by ADR-0039, never configured.

    Returns:
        A `guard.tier_floor.night_veil_initiation` refusal, or None when it does not apply or
        every fact holds.
    """
    del policy  # ADR-0039 fixes the initiation rule; nothing in [guard] may loosen it.
    if request.point not in INITIATION_POINTS or not is_night_veil(request.context):
        return None
    problem = _missing_fact(request.context)
    if problem is None:
        return None
    return tier(
        INITIATION_FLOOR,
        f"Night Veil work starts only from a human's own goal request naming NIGHT_VEIL, and "
        f"{problem}",
    )


def _missing_fact(context: PolicyContext) -> str | None:
    """Say which initiation fact does not hold, or return None when all three do."""
    # The task itself: a Queen- or Warden-originated task never initiates the tier.
    if context.origin is not RequestOrigin.HUMAN:
        origin = context.origin.value if context.origin is not None else "unknown"
        return f"this task's origin is {origin}"
    facts = context.goal_request
    # No durable request: the tier came from a planner or a hand-written graph, not a human ask.
    if facts is None:
        return "this task cites no goal request"
    if facts.origin is not RequestOrigin.HUMAN:
        return f"its goal request's origin is {facts.origin.value}"
    if facts.comb_shield is not CombShieldLevel.NIGHT_VEIL:
        asked = facts.comb_shield.name if facts.comb_shield is not None else "no tier"
        return f"its goal request asked for {asked}"
    return None
