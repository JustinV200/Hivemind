"""Check a quarantine at the Guard's `quarantine` enforcement point, or refuse it with a reason.

ADR-0039 gives every state-changing action a named enforcement point; ADR-0043's quarantine (roadmap
step 10.6c) is checked at `EnforcementPoint.QUARANTINE` through this Warden's `Enforcer`, with the
principal that ordered it (the Queen, or this Warden itself). What a quarantine needs is authority
over the Cell (the unit of compute this Warden supervises) the bee runs on: the capability that
Cell's lease needs (`WardenDeps.lease_capability`: `cell:hive_stand`, `cell:virtual`,
`cell:real:<node>`), which the Queen holds for every Cell and a Warden for its own, and which no
Worker holds. Two refusals the held set cannot express are reached here and recorded the same way
(`Enforcer.refuse`, a `guard.denied` row with a `guard.scope.<why>` rule): an order naming no
sub-bee this Warden supervises (`guard.scope.sub_bee`), and a respawn of a quarantined task that
does not resume from its own quarantine checkpoint cleared by a judge
(`guard.scope.quarantine_checkpoint`), the only way out ADR-0043 allows.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    quarantine sub-package. Called by `hivemind.wardens.quarantine.path` before anything is cut,
    and by `hivemind.wardens.quarantine.gate` for a refused respawn. Calls into `hivemind.guard`
    only (and reads the Warden's own Cell for the request's context).

Key invariants:
    - Nothing is checkpointed, stopped, revoked or tainted unless `authorize` returned True.
    - Every refusal here is a `guard.denied` row before the caller sees it.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the points.
    - hivemind.guard.policy.catalogue, where `warden.intervened` is authorised at this point.
    - tests/unit/guard/policy/test_catalogue.py, whose call-site registry names `authorize`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.guard import (
    QUEEN_ROLE,
    CapabilitySet,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    PrincipalRef,
    queen_principal,
    role_set,
)
from hivemind.wardens.quarantine.order import QuarantineOrder

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

SUB_BEE_SCOPE = "sub_bee"  # The rule suffix for an order naming no sub-bee of this Warden.
CHECKPOINT_SCOPE = "quarantine_checkpoint"  # The rule suffix for a refused way out of quarantine.

__all__ = ["CHECKPOINT_SCOPE", "SUB_BEE_SCOPE", "authorize", "refuse_respawn"]


async def authorize(warden: Warden, order: QuarantineOrder, sub_bee: SubBee | None) -> bool:
    """Pass the `quarantine` point for `order`, or refuse it; a refusal is already on the trail.

    Args:
        warden: The Warden asked to carry the quarantine out.
        order: Who ordered it, what they hold, and the lever naming the bee.
        sub_bee: The live sub-bee the order names, or None when it names none of this Warden's.

    Returns:
        True only when the order names one of this Warden's sub-bees and the orderer holds the
        capability of the Cell that bee runs on.
    """
    request = _request(warden, order.ordered_by, order.held)
    # A quarantine acts on one sub-bee this Warden supervises; naming none is out of its reach
    # whatever the orderer holds, and says so on the trail rather than silently doing nothing.
    if sub_bee is None:
        named = order.lever.bee or order.lever.task_id
        why = f"no sub-bee of warden {warden._warden_id} matches {named}"
        await warden._deps.enforcer.refuse(request, SUB_BEE_SCOPE, why)
        return False
    return (await warden._deps.enforcer.check(request)).allowed


async def refuse_respawn(warden: Warden, why: str) -> None:
    """Record why a respawn of a quarantined task was refused (`guard.scope.<CHECKPOINT_SCOPE>`).

    A respawn is a `TaskAssign`, which only the Queen sends a Warden, so she is the principal.

    Args:
        warden: The Warden that refused it.
        why: The end of the reason sentence, naming ids only.
    """
    deps = warden._deps
    queen = queen_principal(deps.identity.hive_id)
    request = _request(warden, queen, role_set(deps.guard, QUEEN_ROLE))
    await deps.enforcer.refuse(request, CHECKPOINT_SCOPE, why)


def _request(warden: Warden, principal: PrincipalRef, held: CapabilitySet) -> PolicyRequest:
    """Build the `quarantine` check: the orderer must hold the Cell the bee runs on."""
    return PolicyRequest(
        principal=principal,
        point=EnforcementPoint.QUARANTINE,
        needed=warden._deps.lease_capability,
        held=held,
        context=_context(warden),
    )


def _context(warden: Warden) -> PolicyContext:
    """Return the Cell's tier and access level, or an empty context before any lease."""
    cell = warden._cell
    if cell is None:
        return PolicyContext()
    return PolicyContext(comb_shield=cell.comb_shield, access_level=cell.access_level)
