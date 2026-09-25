"""Check an isolation at the Guard's `isolation` enforcement point, or refuse it with a reason.

ADR-0039 gives every state-changing action a named enforcement point; isolating a Cell (roadmap
step 10.6a, ADR-0043) is checked at `EnforcementPoint.ISOLATION` through the Queen's `Enforcer`,
with the principal that ordered it: the Queen (holding her `queen` role set) or the human (the
operator's set). What an isolation needs is authority over the Cell itself: the capability that
Cell asks of anything placed on it (`cell:hive_stand`, `cell:virtual`, `cell:real:<cell>`, the
same answer placement's own rule gives, `hivemind.queen.placement.rules.placement_needs`). One
refusal the held set cannot express is reached here and recorded the same way (`Enforcer.refuse`,
a `guard.denied` row with rule `guard.scope.hive_stand`): the Queen isolating the Hive Stand's own
lease, which ADR-0043 leaves to the human alone.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by `hivemind.queen.isolation.path` before anything is cut.
    Calls into `hivemind.cell` (Cell), `hivemind.guard` (the policy request models, principals,
    role sets), `hivemind.queen.placement` (RealCandidate, rules), the sub-package's own order and
    waggle only; `QueenDeps` only for its type.

Key invariants:
    - Nothing is revoked, paused, written, cut or tainted unless `authorize_isolation` allowed it.
    - Every refusal here is a `guard.denied` row before the caller sees it.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the points.
    - hivemind.guard.policy.catalogue, where `cell.isolated` is authorised at this point.
    - tests/unit/guard/policy/test_catalogue.py, whose call-site registry names this function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import Cell
from hivemind.guard import (
    QUEEN_ROLE,
    Capability,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
    queen_principal,
    role_set,
)
from hivemind.guard.policy import OPERATOR_ID
from hivemind.queen.isolation.order import IsolationOrder, IsolationRefusal, Isolator
from hivemind.queen.placement import RealCandidate, rules

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink

HIVE_STAND_SOURCE = "hive_stand"  # The Hive Stand's Cell source, as placement's snapshot reads it.
HIVE_STAND_SCOPE = "hive_stand"  # The rule suffix for the Queen reaching for the Hive Stand.
OPERATOR_ROLE = "operator"  # The [roles.operator] table: the human's own set, the root of all.

__all__ = [
    "HIVE_STAND_SCOPE",
    "HIVE_STAND_SOURCE",
    "OPERATOR_ROLE",
    "authorize_isolation",
    "cell_capability",
    "is_hive_stand",
]


async def authorize_isolation(
    deps: QueenDeps, link: WardenLink, order: IsolationOrder
) -> IsolationRefusal | None:
    """Pass the `isolation` point for `order`, or refuse it; a refusal is already on the trail.

    Args:
        deps: The Queen's collaborators; `enforcer` checks and records.
        link: The attached Warden whose Cell `order` names.
        order: Who ordered it and why.

    Returns:
        None when allowed; HIVE_STAND when the Queen reached for the Hive Stand, NOT_HELD when the
        orderer does not hold the Cell's own capability.
    """
    request = _request(deps, link, order.ordered_by)
    # ADR-0043: "the Hive Stand's own lease is isolated only by the human". Her set holds
    # cell:hive_stand (she places work there), so the set cannot say this; the point does.
    if order.ordered_by is Isolator.QUEEN and is_hive_stand(link.cell):
        why = f"the Hive Stand's own lease ({link.cell.id}) is isolated only by the human"
        await deps.enforcer.refuse(request, HIVE_STAND_SCOPE, why)
        return IsolationRefusal.HIVE_STAND
    decision = await deps.enforcer.check(request)
    return None if decision.allowed else IsolationRefusal.NOT_HELD


def is_hive_stand(cell: Cell) -> bool:
    """Return whether `cell` is the Hive Stand's own (by its source, as placement reads it).

    Args:
        cell: An attached Warden's Cell.

    Returns:
        True for the Hive Stand.
    """
    return cell.source == HIVE_STAND_SOURCE


def cell_capability(link: WardenLink) -> Capability:
    """Return the capability `link`'s Cell asks of whoever acts on it: placement's own answer.

    Args:
        link: An attached Warden and its Cell.

    Returns:
        `cell:hive_stand`, `cell:virtual` or `cell:real:<cell id>`.
    """
    # Placement is the one caller codingrules 8.7 lets read a Cell's kind; its rule answers here
    # too, so isolation never branches on kind itself (the kind is copied, never compared).
    cell = link.cell
    candidate = RealCandidate(
        warden_id=link.warden_id,
        cell_id=cell.id,
        capabilities=cell.capabilities,
        comb_shield=cell.comb_shield,
        is_hive_stand=is_hive_stand(cell),
        has_free_capacity=True,
        kind=cell.kind,
    )
    return rules.placement_needs(candidate)[0]


def _request(deps: QueenDeps, link: WardenLink, ordered_by: Isolator) -> PolicyRequest:
    """Build the `isolation` check: the orderer must hold the Cell's own capability."""
    policy = deps.enforcer.policy
    cell = link.cell
    if ordered_by is Isolator.HUMAN:
        principal = PrincipalRef(kind=PrincipalKind.OPERATOR, id=OPERATOR_ID, role=OPERATOR_ROLE)
        held = role_set(policy, OPERATOR_ROLE)
    else:
        principal = queen_principal(deps.identity.hive_id)
        held = role_set(policy, QUEEN_ROLE)
    return PolicyRequest(
        principal=principal,
        point=EnforcementPoint.ISOLATION,
        needed=cell_capability(link),
        held=held,
        context=PolicyContext(comb_shield=cell.comb_shield, access_level=cell.access_level),
    )
