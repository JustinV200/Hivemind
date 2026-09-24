"""Define authorize_binding: the slot_binding enforcement point, before a sub-bee is bound.

Roadmap step 10.3 (ADR-0031): binding a sub-bee to a model is `llm:<slot>`, and a binding must stay
inside two limits at once -- the Forage grant it runs under (a grant names the slots its sub-bees
may draw on, `GrantIssued.allowed`) and the sub-bee's own capability set (a Drone holds
`llm:worker`, never `llm:queen`). Every binding goes through here: a sub-bee's first binding at
spawn, a Warden's own REBIND, and a Queen-sent `Intervene(REBIND)`, which before this step was
never checked against the grant at all. The `[llm.slots]` key being bound is first resolved to the
slot it serves (`hivemind.forage.map.slot_for_binding`: a slot's own key, or the slot whose
fallback chain names it); the principal that ordered the binding (the Warden, or the Queen) then
holds, for this action, exactly the `llm:<slot>` capabilities the grant names and the sub-bee's set
allows, and the Guard's `Enforcer` decides and records any refusal. A key that serves no slot at
all is refused outright (`guard.scope.binding_key`): nothing can be checked about it.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside `wardens.spawn`. Called by
    `hivemind.wardens.spawn.spawn.spawn_sub_bee` (every binding it resolves) and by
    `hivemind.wardens.ticks.alarms` (a rebind's check, before the old sub-bee is retired). Calls
    into `hivemind.forage` (ModelSlot, slot_for_binding), `hivemind.guard`,
    `hivemind.wardens.deps` (WardenDeps) and waggle (GrantIssued) only.

Key invariants:
    - Allowed only when the key's slot is both named by the grant and allowed by the sub-bee's set:
      the held set is their intersection, so neither limit can be bypassed by the other.
    - Pure apart from the Enforcer's own trail write on a refusal; nothing is bound or spawned here.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for slot binding.
    - hivemind.forage.map.slot_for_binding for how a named binding resolves to its slot.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.forage import ModelSlot, slot_for_binding
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    EnforcementPoint,
    PolicyContext,
    PolicyDecision,
    PolicyRequest,
    PrincipalRef,
)
from hivemind.wardens.deps import WardenDeps
from waggle.messages.forage import GrantIssued

__all__ = ["BindingCheck", "authorize_binding"]


@dataclass(frozen=True, slots=True)
class BindingCheck:
    """One binding to authorise (codingrules 5.1's grouping of `authorize_binding`'s inputs).

    Attributes:
        orderer: Who ordered the binding: the Warden (a spawn or its own REBIND) or the Queen (an
            `Intervene(REBIND)`).
        capabilities: The sub-bee's own capability set, as `worker_capabilities` computed it.
        grant: The Forage grant the sub-bee runs under; its `allowed` slots bound every binding.
        binding_key: The `[llm.slots]` key the sub-bee is about to be bound to.
        slot: The slot the sub-bee's assignment names; what a key that serves no known slot is
            recorded as needing when it is refused.
        context: Where the binding happens: the Cell's tier and access level.
    """

    orderer: PrincipalRef
    capabilities: CapabilitySet
    grant: GrantIssued
    binding_key: str
    slot: ModelSlot
    context: PolicyContext


async def authorize_binding(deps: WardenDeps, check: BindingCheck) -> PolicyDecision:
    """Pass the `slot_binding` point for `check.binding_key`, or refuse it with a reason.

    Args:
        deps: The Warden's collaborators; `bindings` resolves the key, `enforcer` decides.
        check: The binding, the sub-bee's set, the grant, who ordered it and where.

    Returns:
        The Enforcer's decision; a refusal is already `guard.denied` on the trail.
    """
    slot = slot_for_binding(check.binding_key, deps.bindings)
    needed = _llm(slot if slot is not None else check.slot)
    request = PolicyRequest(
        principal=check.orderer,
        point=EnforcementPoint.SLOT_BINDING,
        needed=needed,
        held=_grant_slots_held(check),
        context=check.context,
    )
    if slot is None:
        why = f"no [llm.slots] row serves the key {check.binding_key!r}"
        return await deps.enforcer.refuse(request, "binding_key", why)
    return await deps.enforcer.check(request)


def _grant_slots_held(check: BindingCheck) -> CapabilitySet:
    """Return `llm:<slot>` for every slot the grant names that the sub-bee's own set allows."""
    named = {ModelSlot.from_wire(binding.slot) for binding in check.grant.allowed}
    kept = frozenset(_llm(slot) for slot in named if check.capabilities.allows(_llm(slot)))
    return CapabilitySet(capabilities=kept)


def _llm(slot: ModelSlot) -> Capability:
    """Return `llm:<slot>`, the capability binding `slot` needs (its lowercase member name)."""
    return Capability(family=CapabilityFamily.LLM, scope=slot.manifest_key)
