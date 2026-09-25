"""Define sub_bee_capabilities: one sub-bee's capability slice, read from its TaskAssign.

A Worker's set is its role default plus what its task needs, kept only where its Warden's set and
its goal's set both allow it (ADR-0039). `hivemind.workers.capabilities.worker_capabilities` does
that narrowing; this module reads its inputs off the assignment a Warden is spawning: the role
default from the Warden's own Guard policy (`role_set`, `{scratch}` filled with the lease's scratch
root), the task's network needs (Waggle 1.8's `TaskAssign.network_scopes`, roadmap step 10.3 --
before it, a task's network needs never reached its Warden, so no Worker ever held `net`), the
goal's set (`TaskAssign.capabilities`; None when the goal carries no ceiling) and the declared
write roots (roadmap step 5.0e: the manifest's `keep_root` and each declared leaving's root).
Anything on the wire this Warden cannot read fails closed: a goal set with a string that is not a
capability allows nothing, and a network scope that is not a host, a domain or an address is never
offered, so a malformed assignment can refuse a Worker capabilities but never crash the Warden.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside `wardens.spawn`. Called by
    `hivemind.wardens.spawn.spawn.spawn_sub_bee`, once per spawn. Calls into `hivemind.cell`
    (TaskNeeds, MAX_SCOPE_CHARS), `hivemind.common.logging`, `hivemind.forage` (Tempo),
    `hivemind.guard`, `hivemind.supervision.capping.leave` (declared_leaving_root),
    `hivemind.wardens.deps` (WardenDeps), `hivemind.workers` (worker_capabilities) and waggle only.

Key invariants:
    - The slice is never wider than the Warden's set, nor (when the goal carries one) the goal's:
      `worker_capabilities` proves the first by `attenuate` and filters by both.
    - An unreadable goal set narrows to nothing, never to "no ceiling".

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md, "A goal carries a
      ceiling".
    - hivemind.workers.capabilities for worker_capabilities, the narrowing itself.
"""

from __future__ import annotations

from pathlib import Path, PurePath

from hivemind.cell import MAX_SCOPE_CHARS, TaskNeeds
from hivemind.common.logging import get_logger
from hivemind.forage.tempo import Tempo
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    InvalidCapabilityError,
    role_set,
    worker_role_name,
)
from hivemind.supervision.capping.leave import declared_leaving_root
from hivemind.wardens.deps import WardenDeps
from hivemind.workers import worker_capabilities
from waggle.messages.task import TaskAssign

_LOG = get_logger(__name__)

__all__ = ["declared_write_roots", "sub_bee_capabilities"]


def sub_bee_capabilities(
    deps: WardenDeps, ceiling: CapabilitySet, scratch_root: PurePath, assignment: TaskAssign
) -> tuple[CapabilitySet, tuple[Path, ...]]:
    """Compute one sub-bee's capability slice, and the declared write roots it shares.

    Args:
        deps: The spawning Warden's collaborators; `guard` holds the role defaults.
        ceiling: The Warden's own set (`hivemind.guard.warden_set`), the widest a slice may be.
        scratch_root: The lease's scratch directory, filling the role default's `{scratch}`.
        assignment: The TaskAssign being spawned: its role, tempo, network scopes and goal set.

    Returns:
        `(capabilities, write_roots)`: the slice, and the roots `write_roots` also widens the
        lease's reachability to (`hivemind.wardens.spawn.spawn._widen_lease_reachability`).
    """
    write_roots = declared_write_roots(deps, assignment)
    role_default = role_set(deps.guard, worker_role_name(assignment.role), scratch_root)
    capabilities = worker_capabilities(
        ceiling,
        role_default,
        _task_needs(assignment),
        extra_write_roots=write_roots,
        goal=_goal_set(assignment),
    )
    return capabilities, write_roots


def declared_write_roots(deps: WardenDeps, assignment: TaskAssign) -> tuple[Path, ...]:
    """Return the manifest's own `keep_root` plus each of `assignment`'s declared leaving roots.

    Roadmap step 5.0e: the one set of roots both `worker_capabilities` (an outside-scratch
    `fs:write` and `cell:outside_scratch` candidate per root) and the lease's reachability need;
    computed once per spawn so neither repeats `declared_leaving_root`'s own `~`-expansion.

    Args:
        deps: The spawning Warden's collaborators; `leave_home` and `keep_root` are read.
        assignment: The TaskAssign being spawned; its `leaves` are read.

    Returns:
        Every declared root, keep root last when one is set.
    """
    roots = [
        declared_leaving_root(leaving.pattern, deps.leave_home) for leaving in assignment.leaves
    ]
    if deps.keep_root is not None:
        roots.append(deps.keep_root)
    return tuple(roots)


def _task_needs(assignment: TaskAssign) -> TaskNeeds:
    """Build the TaskNeeds `worker_capabilities` reads: the tempo and every readable scope."""
    scopes = tuple(scope for scope in assignment.network_scopes if _is_net_scope(scope))
    return TaskNeeds(tempo=Tempo.from_wire(assignment.tempo), network_scopes=scopes)


def _is_net_scope(scope: str) -> bool:
    """Return whether `scope` is a `net` scope a Worker could hold; log and skip one that is not."""
    try:
        Capability(family=CapabilityFamily.NET, scope=scope)
    except ValueError:
        _LOG.warning("warden.network_scope_unreadable", scope_chars=len(scope))
        return False
    return len(scope) <= MAX_SCOPE_CHARS


def _goal_set(assignment: TaskAssign) -> CapabilitySet | None:
    """Parse the goal's set off the wire: None stays None, and an unreadable one allows nothing."""
    if assignment.capabilities is None:
        return None  # No ceiling travels: the operator's own local submission.
    try:
        return CapabilitySet.parse(*assignment.capabilities)
    except InvalidCapabilityError:
        # Fail closed: a ceiling this Warden cannot read must narrow, never vanish.
        _LOG.warning("warden.goal_set_unreadable", task_id=assignment.task_id)
        return CapabilitySet.empty()
