"""Define worker_for: build the right Worker implementation for a TaskAssign.role.

Both composition roots (`hivemind.cli.compose.deps.build_warden_deps` and `hivemind.cli.in_cell.
deps.build_in_cell_warden_deps`) used to define an identical `_build_drone(role) -> Drone()`,
ignoring `role` entirely, back when the Drone was the only role a Warden could ever spawn. Roadmap
step 6.9 gives the Forager a Worker implementation and step 6.10 the Scout; this module is the one
place that maps a `waggle.messages.task.WorkerRole` to a fresh instance of whichever role
implements it, so both composition roots wire `WardenDeps.worker_factory` to this single function
instead of each hard-coding the same mapping. `GuardBee`, `HouseBee` and `Undertaker` are not
spawned this way at all -- a `HouseBee` runs on the Queen's or a Warden's own sweep timer, never
through a `TaskAssign` (see that role's own package docstring), and `GuardBee` has no
implementation yet -- so a `TaskAssign.role` naming any of them is a Queen-side bug this function
refuses rather than silently mishandles.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles`. Called by both composition
    roots (`hivemind.cli.compose.deps`, `hivemind.cli.in_cell.deps`) as `WardenDeps.
    worker_factory`. Calls into `hivemind.workers.base`, `hivemind.workers.errors`, this package's
    `drone`, `forager` and `scout` sub-packages, and waggle only.

Key invariants:
    - Every call returns a fresh instance: a Warden's `worker_factory` is called once per attempt
      (roadmap step 3.19), so nothing here may be shared or reused across attempts.
    - Every `WorkerRole` not handled by name raises `UnsupportedWorkerRoleError`, never falls
      through to a default role.

See Also:
    - .claude/roadmap.md step 6.9 for the Forager, and step 6.10 for the Scout.
    - hivemind.wardens.deps for WardenDeps.worker_factory, this function's one wire-up point.
    - hivemind.workers.roles.drone, .forager and .scout for the three roles this function builds.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import ClassVar

from hivemind.workers.base import Worker
from hivemind.workers.errors import WorkerError
from hivemind.workers.roles.drone import Drone
from hivemind.workers.roles.forager import Forager
from hivemind.workers.roles.scout import Scout
from waggle.messages.task import WorkerRole

# WHY: a registry, not an if chain (codingrules 8.4): a new role is one more row here. Each value
# builds a fresh instance, because a Worker is built per attempt and never shared.
_WORKERS: Mapping[WorkerRole, Callable[[], Worker]] = {
    WorkerRole.DRONE: Drone,
    WorkerRole.FORAGER: Forager,
    WorkerRole.SCOUT: Scout,
}

__all__ = ["UnsupportedWorkerRoleError", "worker_for"]


class UnsupportedWorkerRoleError(WorkerError):
    """Raise when a `TaskAssign.role` names a role no Warden spawns through the wire.

    `hivemind.workers.roles.worker_for`'s one error: a Queen never assigns GUARD_BEE, HOUSE_BEE
    or UNDERTAKER through a `TaskAssign` (module docstring), so seeing one here is a Queen-side
    bug, not a condition a Warden can recover from by retrying.
    """

    code: ClassVar[str] = "hivemind.workers.roles.unsupported_role"

    def __init__(self, role: WorkerRole) -> None:
        """Build the error naming the unsupported role.

        Args:
            role: The `WorkerRole` `worker_for` was asked to build and does not know.
        """
        super().__init__(
            f"No Worker is spawned for role {role.value!r} through a TaskAssign; only DRONE, "
            "FORAGER and SCOUT are."
        )
        self.role = role


def worker_for(role: WorkerRole) -> Worker:
    """Build a fresh Worker implementation for `role`.

    Args:
        role: The role a `TaskAssign` names.

    Returns:
        A fresh `Drone`, `Forager` or `Scout`, matching `role`.

    Raises:
        UnsupportedWorkerRoleError: `role` is GUARD_BEE, HOUSE_BEE or UNDERTAKER -- none of which
            a Queen ever assigns through a TaskAssign (module docstring).
    """
    build = _WORKERS.get(role)
    if build is None:
        raise UnsupportedWorkerRoleError(role)
    return build()
