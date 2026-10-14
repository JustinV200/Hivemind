"""Define authorise_rung: ask the Guard whether the Queen may move a device up or down the ladder.

Every rung change, up or down, passes the Guard's `NUC_PROMOTION` enforcement point (ADR-0052): the
Queen acts for the Hive, and the policy decides whether the set she holds covers touching this
device. She asks for `device:<device_id>`, which her default set holds in full (`device:*`) and an
operator can narrow, to refuse a device or a class of them by policy. The check names the device's
Comb Shield tier and access level so a floor can read them, and a refusal is a `guard.denied` on the
trail, recorded by the Enforcer, before the caller says it was refused.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside
    `hivemind.queen.swarm_gate.colonize`. Called by `.flow` before it touches a device, for a
    promotion and for a withdrawal alike. Calls into `hivemind.guard` (the capability and the
    enforcement point), `hivemind.queen.authority`, `hivemind.queen.deps` and `hivemind.swarm`.

Key invariants:
    - Nothing is sent to the device, paused or stopped before this has answered yes.
    - The same call guards both directions: a rung down is no less a change to a borrowed machine.
    - A refusal costs nothing but its `guard.denied` row: the check changes no state.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the Guard's points.
    - hivemind.guard.policy.catalogue, where `swarm.promoted` and `swarm.demoted` are authorised
      at this point.
"""

from __future__ import annotations

from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint
from hivemind.queen.authority import queen_held, request_for
from hivemind.queen.deps import QueenDeps
from hivemind.swarm import SwarmNode

__all__ = ["authorise_rung"]


async def authorise_rung(deps: QueenDeps, node: SwarmNode) -> bool:
    """Whether the Guard lets the Queen change `node`'s rung.

    Args:
        deps: The Queen's collaborators; her Enforcer decides and records a refusal.
        node: The device whose rung is about to change.

    Returns:
        True when the Queen's set covers the device at the `nuc_promotion` point.
    """
    needed = Capability(family=CapabilityFamily.DEVICE, scope=node.device_id)
    request = request_for(deps, EnforcementPoint.NUC_PROMOTION, needed, queen_held(deps))
    # The device's tier and access level are where the change lands: what a floor would read.
    context = request.context.model_copy(
        update={"comb_shield": node.comb_shield, "access_level": node.access_level}
    )
    decision = await deps.enforcer.check(request.model_copy(update={"context": context}))
    return decision.allowed
