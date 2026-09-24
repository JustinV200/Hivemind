"""Build Guard test data: a principal of any kind, and a policy request around one need.

The floors (`hivemind.guard.policy.floors`, roadmap steps 10.3a-10.3d) are tested request by
request: who asks (a bee, the Queen, the operator), at which point, needing which capability,
holding what, and in which context. `make_principal` builds a valid `PrincipalRef` for any kind
(each kind acts under its own id kind), and `make_request` wraps one need into a `PolicyRequest`
with sensible defaults, so a test states only the fact under test.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the guard unit tests.

Key invariants:
    - Every value built here passes the models' own validators.

See Also:
    - hivemind.guard.policy.models for the shapes built here.
"""

from __future__ import annotations

from hivemind.guard.capabilities import Capability, CapabilitySet
from hivemind.guard.policy.models import (
    OPERATOR_ID,
    PolicyContext,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
)
from hivemind.guard.policy.points import EnforcementPoint
from waggle.clock import FakeClock
from waggle.ids import IdKind, new_id

# The id kind each principal kind acts as, and the policy role a test gives it by default.
_ID_KINDS = {
    PrincipalKind.QUEEN: (IdKind.HIVE, "queen"),
    PrincipalKind.WARDEN: (IdKind.WARDEN, "warden"),
    PrincipalKind.WORKER: (IdKind.WORKER, "drone"),
    PrincipalKind.SWARM_DEVICE: (IdKind.DEVICE, "swarm_device"),
    PrincipalKind.CLIENT_DEVICE: (IdKind.DEVICE, "device"),
}

__all__ = ["make_principal", "make_request"]


def make_principal(kind: PrincipalKind = PrincipalKind.WORKER) -> PrincipalRef:
    """Build a valid principal of `kind`, under the policy role that kind usually has.

    Args:
        kind: Who acts; a Worker (a Drone) by default.

    Returns:
        A PrincipalRef whose id matches its kind (the operator is the literal "human").
    """
    if kind is PrincipalKind.OPERATOR:
        return PrincipalRef(kind=kind, id=OPERATOR_ID, role="operator")
    id_kind, role = _ID_KINDS[kind]
    return PrincipalRef(kind=kind, id=new_id(id_kind, FakeClock()), role=role)


def make_request(
    needed: str,
    *held: str,
    kind: PrincipalKind = PrincipalKind.WORKER,
    point: EnforcementPoint = EnforcementPoint.TOOL_INVOCATION,
    context: PolicyContext | None = None,
) -> PolicyRequest:
    """Build a request for one capability string, held or not.

    Args:
        needed: The capability the action needs, as a string (`"net:127.0.0.1"`).
        *held: What the principal holds, as capability strings; nothing by default.
        kind: Who acts; a Worker by default.
        point: Where; tool invocation by default.
        context: Where the action happens; an empty context by default.

    Returns:
        A validated PolicyRequest.
    """
    return PolicyRequest(
        principal=make_principal(kind),
        point=point,
        needed=Capability.parse(needed),
        held=CapabilitySet.parse(*held),
        context=context or PolicyContext(),
    )
