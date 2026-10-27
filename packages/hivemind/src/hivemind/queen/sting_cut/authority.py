"""Check a Sting Cut at the Guard's `sting_cut` enforcement point, or refuse it with a reason.

ADR-0039 gives every state-changing action a named enforcement point; cutting a Cell off (roadmap
step 13.4a) is checked at `EnforcementPoint.STING_CUT` through the Queen's `Enforcer`, needing the
`sting_cut` flag, which the operator's and the Queen's roles hold and no bee's does. Two refusals
the held set cannot express are reached here and recorded the same way (`Enforcer.refuse`, a
`guard.denied` row): the Queen reaching for the Hive Stand's own lease, which stays the human's
alone (rule `guard.scope.hive_stand`), and the Queen past `max_autonomous_sting_cuts_per_hour`
(rule `guard.scope.hourly_cap`). The human is never capped.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Called by `hivemind.queen.sting_cut.path` before anything is cut.
    Calls into `hivemind.guard`, `hivemind.queen.isolation` (OPERATOR_ROLE) and the
    sub-package's own order and record only; `QueenDeps` only for its type.

Key invariants:
    - Nothing is revoked, scrubbed, forgotten or torn down unless `authorize_sting_cut` allowed it.
    - Every refusal here is a `guard.denied` row before the caller sees it.

See Also:
    - hivemind.guard.policy.catalogue, where `cell.sting_cut` is authorised at this point.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import Cell
from hivemind.guard import (
    QUEEN_ROLE,
    Capability,
    CapabilityFamily,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
    queen_principal,
    role_set,
)
from hivemind.guard.policy import OPERATOR_ID
from hivemind.queen.isolation.authority import OPERATOR_ROLE
from hivemind.queen.sting_cut.order import Cutter, StingCutOrder, StingCutRefusal
from hivemind.queen.sting_cut.record import queen_cuts_in_window

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps

HIVE_STAND_SCOPE = "hive_stand"  # The rule suffix for the Queen reaching for the Hive Stand.
HOURLY_CAP_SCOPE = "hourly_cap"  # The rule suffix for the Queen past her hourly cap.

__all__ = ["HIVE_STAND_SCOPE", "HOURLY_CAP_SCOPE", "authorize_sting_cut"]


async def authorize_sting_cut(
    deps: QueenDeps, order: StingCutOrder, cell: Cell | None, hive_stand: bool
) -> StingCutRefusal | None:
    """Pass the `sting_cut` point for `order`, or refuse it; a refusal is already on the trail.

    Args:
        deps: The Queen's collaborators; `enforcer` checks and records, `guard` holds the cap.
        order: Who ordered it and why.
        cell: The Cell, when its Warden is attached; its tier and access level go on the context.
        hive_stand: Whether the Cell is the Hive Stand's own.

    Returns:
        None when allowed; HIVE_STAND or HOURLY_CAP for the Queen; NOT_HELD when the orderer
        does not hold `sting_cut`.
    """
    request = _request(deps, order.cut_by, cell)
    if order.cut_by is Cutter.QUEEN:
        # Roadmap 13.4a: "never for the Hive Stand's lease, which stays human-only".
        if hive_stand:
            why = f"the Hive Stand's own lease ({order.cell_id}) is cut only by the human"
            await deps.enforcer.refuse(request, HIVE_STAND_SCOPE, why)
            return StingCutRefusal.HIVE_STAND
        cap = deps.guard.max_sting_cuts_per_hour
        if await queen_cuts_in_window(deps) >= cap:
            why = f"the Queen already cut {cap} Cell(s) in the last hour"
            await deps.enforcer.refuse(request, HOURLY_CAP_SCOPE, why)
            return StingCutRefusal.HOURLY_CAP
    decision = await deps.enforcer.check(request)
    return None if decision.allowed else StingCutRefusal.NOT_HELD


def _request(deps: QueenDeps, cut_by: Cutter, cell: Cell | None) -> PolicyRequest:
    """Build the `sting_cut` check: the orderer must hold the `sting_cut` flag."""
    policy = deps.enforcer.policy
    if cut_by is Cutter.HUMAN:
        principal = PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role=OPERATOR_ROLE)
        held = role_set(policy, OPERATOR_ROLE)
    else:
        principal = queen_principal(deps.identity.hive_id)
        held = role_set(policy, QUEEN_ROLE)
    context = (
        PolicyContext(comb_shield=cell.comb_shield, access_level=cell.access_level)
        if cell is not None
        else PolicyContext()
    )
    return PolicyRequest(
        principal=principal,
        point=EnforcementPoint.STING_CUT,
        needed=Capability(family=CapabilityFamily.STING_CUT),
        held=held,
        context=context,
    )
