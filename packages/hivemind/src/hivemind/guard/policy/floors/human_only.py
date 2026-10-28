"""Refuse every principal but the human at a human-only point: the human-only floor (roadmap 13.4).

Codingrules 15: "Absconding is human-only and break-glass: ... it is never callable by Queen,
Wardens, Workers, autopilot, or tools." A held set cannot say that by itself, because a manifest's
`[guard.roles.<role>]` may replace any role's `allow` list, `abscond` included. So, like every
floor (ADR-0039, "Floors hold whatever a set says"), this one reads who is acting, never what it
holds: at a point in `HUMAN_ONLY_POINTS`, any principal whose kind is not OPERATOR is refused
under `guard.state_floor.human_only`, whatever its set. The human still needs `abscond` held
(the operator role's default), so the held-set rule answers last as everywhere else.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.guard.policy.floors`.
    Run by `hivemind.guard.policy.floors.chain`, first. Calls into this package's `refusal` and
    the policy package's `models`, `points` and `table`.

Key invariants:
    - Applies only at `HUMAN_ONLY_POINTS`; refuses and never allows.
    - Never configured: nothing in `[guard]` may add a principal kind to the human's.

See Also:
    - .claude/codingrules.md section 15, "Blast radius defaults".
    - hivemind.cli.abscond for the one caller of the Absconding point.
    - hivemind.queen.supersedure.door for the one caller of the Supersedure point.
"""

from __future__ import annotations

from hivemind.guard.policy.floors.refusal import FloorRefusal, state
from hivemind.guard.policy.models import PolicyRequest, PrincipalKind
from hivemind.guard.policy.points import EnforcementPoint
from hivemind.guard.policy.table import GuardPolicy

HUMAN_ONLY_FLOOR = "human_only"  # guard.state_floor.human_only.
# The points only a person may pass: tearing the whole Hive down (codingrules 15), and moving the
# Hive Stand (codingrules 8.16: never an autopilot rule or an awake decision; ADR-0065).
HUMAN_ONLY_POINTS = frozenset({EnforcementPoint.ABSCONDING, EnforcementPoint.SUPERSEDURE})

__all__ = ["HUMAN_ONLY_FLOOR", "HUMAN_ONLY_POINTS", "human_only_floor"]


def human_only_floor(request: PolicyRequest, policy: GuardPolicy) -> FloorRefusal | None:
    """Refuse a human-only point to every principal that is not the operator.

    Args:
        request: The action: its point and principal are read.
        policy: Unused: who is human is fixed by codingrules 15, never configured.

    Returns:
        A `guard.state_floor.human_only` refusal, or None when the point is not human-only or
        the operator acts.
    """
    del policy  # Codingrules 15 fixes it; no [guard] table may loosen it.
    if request.point not in HUMAN_ONLY_POINTS or request.principal.kind is PrincipalKind.OPERATOR:
        return None
    return state(
        HUMAN_ONLY_FLOOR,
        f"only the human may act at {request.point.value}, never a {request.principal.kind.value}",
    )
