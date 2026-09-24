"""Define authorize_grant: the grant_issue enforcement point, before a fresh grant is ever sent.

Roadmap step 10.3 (ADR-0031): the Queen issues a `ForageGrant` to one Warden for one task, and a
grant names the model bindings (`AllowedBinding`: a slot, the source it draws on) its sub-bees may
be bound to. Binding a slot is `llm:<slot>`, so the Queen, acting at the `grant_issue` point,
checks every binding against the receiving Warden's set (which she computes from its Cell's access
level, `hivemind.queen.authority.warden_held`) and, when the task's goal carries one, the goal's
set: a binding either set does not allow is removed, each removal a `guard.denied` on the trail
through her `Enforcer`, and a seat reservation left naming no binding's source is dropped with it.
What is left is the grant she sends; a grant left with no binding at all is refused by the caller
(`hivemind.queen.dispatcher.ready`) exactly like a grant that allows no sub-bee.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready._send_grant_and_assign` on every fresh
    grant (a first dispatch, a retry, a resume). Calls into `hivemind.brood_chamber` (Task),
    `hivemind.forage` (ForageGrant), `hivemind.guard`, `hivemind.queen.authority` and
    `hivemind.queen.deps` (QueenDeps, WardenLink) only.

Key invariants:
    - The returned grant never names a binding the Warden's set or the goal's set does not allow
      as `llm:<slot>`, and never widens anything: bindings and seats are only ever removed.
    - A grant every binding of which is allowed is returned unchanged (the same object), so a
      grant that needed no narrowing is byte-for-byte the one `hivemind.forage.allocate.grant`
      computed.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the grant_issue
      point.
    - hivemind.forage.allocate for grant, which computes the bindings this narrows.
"""

from __future__ import annotations

from hivemind.brood_chamber import Task
from hivemind.forage import ForageGrant
from hivemind.forage.models import AllowedBinding
from hivemind.guard import Capability, CapabilityFamily, CapabilitySet, EnforcementPoint
from hivemind.queen.authority import goal_held, request_for, task_context, warden_held
from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["authorize_grant"]


async def authorize_grant(
    deps: QueenDeps, link: WardenLink, task: Task, fresh: ForageGrant
) -> ForageGrant:
    """Return `fresh` without every binding the Warden's set or the goal's set does not allow.

    Args:
        deps: The Queen's collaborators; `enforcer` decides and records each refusal.
        link: The receiving Warden's link; its Cell's access level sizes the Warden's set.
        task: The task the grant is issued for; its goal's set, when it carries one, applies too.
        fresh: The grant `hivemind.forage.allocate.grant` just computed.

    Returns:
        `fresh` itself when every binding is allowed; otherwise a copy holding only the allowed
        bindings and the seat reservations on their sources (possibly none of either).
    """
    # Both sets are computed once per grant, not once per binding: they cannot change mid-call.
    held = (warden_held(deps, link.cell), goal_held(task))
    kept = [
        binding
        for binding in fresh.allowed
        if await _binding_allowed(deps, link, task, binding, held)
    ]
    if len(kept) == len(fresh.allowed):
        return fresh
    # A reservation on a source no remaining binding draws on would only hold seats idle.
    sources = {binding.source_id for binding in kept}
    seats = tuple(reservation for reservation in fresh.seats if reservation.source_id in sources)
    return fresh.model_copy(update={"allowed": tuple(kept), "seats": seats})


async def _binding_allowed(
    deps: QueenDeps,
    link: WardenLink,
    task: Task,
    binding: AllowedBinding,
    held: tuple[CapabilitySet, CapabilitySet | None],
) -> bool:
    """Check one binding's `llm:<slot>` against the Warden's set, then the goal's (when present).

    The Warden's set goes first: a binding its own set refuses is refused on that ground alone,
    one `guard.denied` per removed binding, never two.
    """
    needed = Capability(family=CapabilityFamily.LLM, scope=binding.slot.manifest_key)
    context = task_context(task, link.cell)
    for set_held in held:
        if set_held is None:
            continue  # The operator's own goal: no ceiling to check against.
        request = request_for(deps, EnforcementPoint.GRANT_ISSUE, needed, set_held)
        decision = await deps.enforcer.check(request.model_copy(update={"context": context}))
        if not decision.allowed:
            return False
    return True
