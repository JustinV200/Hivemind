"""Build the PrincipalRef each bee acts as at an enforcement point: the Queen, a Warden, a Worker.

Every enforcement point (roadmap step 10.3) names who is acting on its `PolicyRequest`, and the
three bees always act the same way: the Queen as her Hive id under the `queen` policy role, a
Warden as its own id under the `warden` role, and a Worker (a sub-bee) as its own id under the
policy role its wire `WorkerRole` names (`drone`, `guard_bee`, ...). Building those refs in one
place keeps the role names and the id-to-kind pairing (`PrincipalRef`'s own validator) from being
spelled out again at every point. The operator and the two device kinds are built where their
own identities live (the Entrance, roadmap step 10.5), not here.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by every enforcement point in
    `hivemind.queen`, `hivemind.wardens` and `hivemind.workers`. Calls into this package's
    `models` (PrincipalKind, PrincipalRef), `roles` (worker_role_name) and `table` (the role
    names) and waggle (ids, WorkerRole) only.

Key invariants:
    - Pure: each function returns a fresh frozen value and reads nothing else.
    - The role a ref names is always one `GuardPolicy.roles` defines, so a denial on the trail
      always points at a real default set.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "Two roots, and sets
      only narrow below them".
    - hivemind.guard.policy.models for PrincipalRef and its id-matches-kind rule.
"""

from __future__ import annotations

from hivemind.guard.policy.models import PrincipalKind, PrincipalRef
from hivemind.guard.policy.roles import worker_role_name
from hivemind.guard.policy.table import QUEEN_ROLE, WARDEN_ROLE
from waggle.ids import HiveId, WardenId, WorkerId
from waggle.messages.task import WorkerRole

__all__ = ["queen_principal", "warden_principal", "worker_principal"]


def queen_principal(hive_id: HiveId) -> PrincipalRef:
    """Return the ref the Queen acts as: her Hive's own id, under the `queen` role.

    Args:
        hive_id: The Hive the Queen runs (`QueenDeps.identity.hive_id`).

    Returns:
        A QUEEN PrincipalRef.
    """
    return PrincipalRef(kind=PrincipalKind.QUEEN, id=hive_id, role=QUEEN_ROLE)


def warden_principal(warden_id: WardenId) -> PrincipalRef:
    """Return the ref a Warden acts as: its own id, under the `warden` role.

    Args:
        warden_id: The Warden's own id.

    Returns:
        A WARDEN PrincipalRef.
    """
    return PrincipalRef(kind=PrincipalKind.WARDEN, id=warden_id, role=WARDEN_ROLE)


def worker_principal(worker_id: WorkerId, role: WorkerRole) -> PrincipalRef:
    """Return the ref a Worker acts as: its own id, under the policy role its wire role names.

    Args:
        worker_id: The sub-bee's own id.
        role: The `TaskAssign.role` it was spawned for.

    Returns:
        A WORKER PrincipalRef whose role is `worker_role_name(role)` (`"drone"`, ...).
    """
    return PrincipalRef(kind=PrincipalKind.WORKER, id=worker_id, role=worker_role_name(role))
