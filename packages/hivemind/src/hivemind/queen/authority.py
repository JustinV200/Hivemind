"""Name what each principal holds at the Queen's enforcement points: her set, a Warden's, a goal's.

Roadmap step 10.3 wires the Queen's own enforcement points (placement, grant issue, Forage
requests, Warden spawn, question routing and Comb Shield egress, ADR-0031) through her
`hivemind.guard.Enforcer`. Each point asks the same few questions: what the Queen herself holds
(her `queen` role default), what a Warden holds as she sees it (the `warden` role default narrowed
to its Cell's access level, the guard's own `warden_set`), what a goal allows (the capability set
its submitter held, carried on every task, or None for the operator's own local path, which has
no ceiling), and where the action happens (a task's tier and origin, a Cell's tier and access
level). This module answers them once, so every point builds its `PolicyRequest` from the same
facts, and `request_for` assembles the Queen's own request in one call.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    by `hivemind.queen.attach`, `.questions`, `.dispatcher` and `.ticks.forage`. Calls into
    `hivemind.brood_chamber` (Task), `hivemind.cell` (Cell), `hivemind.guard` and
    `hivemind.queen.deps` (QueenDeps) only.

Key invariants:
    - Pure: nothing here records or sends; the caller's `deps.enforcer.check` is what records a
      refusal.
    - A Warden's set is computed exactly as the Warden computes its own (`warden_set`), from its
      Cell's access level; the scratch root only fills `{scratch}` write scopes, which no
      Queen-side point ever needs, so the Hive Stand's own root stands in for every Cell's.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the principals and
      the goal ceiling.
    - hivemind.guard.policy.roles for role_set and warden_set.
"""

from __future__ import annotations

from hivemind.brood_chamber import Task
from hivemind.cell import Cell
from hivemind.guard import (
    QUEEN_ROLE,
    Capability,
    CapabilitySet,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    queen_principal,
    role_set,
    warden_set,
)
from hivemind.queen.deps import QueenDeps

__all__ = ["goal_held", "queen_held", "request_for", "task_context", "warden_held"]


def queen_held(deps: QueenDeps) -> CapabilitySet:
    """Return the Queen's own set: her `queen` role default, which names no scratch directory.

    Args:
        deps: The Queen's collaborators; `enforcer.policy` holds the role defaults.

    Returns:
        Every capability the Queen holds.
    """
    return role_set(deps.enforcer.policy, QUEEN_ROLE)


def warden_held(deps: QueenDeps, cell: Cell) -> CapabilitySet:
    """Return a Warden's set as the Queen computes it, from the access level of its Cell.

    Args:
        deps: The Queen's collaborators; `enforcer.policy` and `scratch_root` are read.
        cell: The Cell the Warden supervises (its `WardenLink.cell`).

    Returns:
        The `warden` role default narrowed to `cell.access_level`, less the deny list.
    """
    return warden_set(deps.enforcer.policy, cell.access_level, deps.scratch_root)


def goal_held(task: Task) -> CapabilitySet | None:
    """Return the capability set `task`'s goal carries, or None when it carries no ceiling.

    Args:
        task: Any task; its `spec.capabilities` is already canonical (the Brood Chamber's own
            validator parsed every string when the task was minted).

    Returns:
        The goal's set, or None for the operator's own local submission (no device ceiling).
    """
    strings = task.spec.capabilities
    return None if strings is None else CapabilitySet.parse(*strings)


def task_context(task: Task, cell: Cell | None = None) -> PolicyContext:
    """Return where `task`'s action happens: its bound tier and origin, and its Cell's facts.

    Args:
        task: The task the action is for.
        cell: The Cell it is on or about to be on, when one is known.

    Returns:
        A PolicyContext the tier floors (roadmap step 10.3a) and the access-level rule read. Its
        `bound_tier` is the tier the task was bound to at assignment (roadmap step 10.3b), or,
        for a task not placed yet, the tier it asks for.
    """
    bound = task.bound_tier if task.bound_tier is not None else task.spec.needs.comb_shield
    return PolicyContext(
        comb_shield=cell.comb_shield if cell is not None else None,
        access_level=cell.access_level if cell is not None else None,
        bound_tier=bound,
        origin=task.spec.origin,
    )


def request_for(
    deps: QueenDeps, point: EnforcementPoint, needed: Capability, held: CapabilitySet
) -> PolicyRequest:
    """Build a request with the Queen as the acting principal and no Cell or task context.

    Args:
        deps: The Queen's collaborators; `identity.hive_id` is who she acts as.
        point: The enforcement point being passed.
        needed: The one capability the action needs.
        held: What the Queen holds for this action: her own set, a goal's, or a Warden's.

    Returns:
        The PolicyRequest; a caller with a task or a Cell adds `context` with `model_copy`.
    """
    return PolicyRequest(
        principal=queen_principal(deps.identity.hive_id),
        point=point,
        needed=needed,
        held=held,
    )
