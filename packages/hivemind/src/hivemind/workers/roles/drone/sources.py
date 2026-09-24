"""Define DroneSources: the Drone's own name for the shared bounded-loop RoleSources.

Roadmap step 6.9 factored every field of this class out to `hivemind.workers.roles.bounded_loop.
sources.RoleSources`, once the Forager and Scout needed the identical hot-state answers a Drone
already had: nothing here was ever Drone-specific. This module keeps the Drone's own name and
import path alive, as a subclass with no added behaviour, so `hivemind.workers.roles.drone.role.
Drone` and this package's own tests (which import `DroneSources` from this exact path) see no
change at all.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.roles.drone`. Built fresh by
    `hivemind.workers.roles.drone.role.Drone.run` for every attempt and handed to `hivemind.
    memory.assemble`. Calls into `hivemind.workers.roles.bounded_loop.sources` only.

Key invariants:
    - None beyond `hivemind.workers.roles.bounded_loop.sources.RoleSources`'s own (see that
      module).

See Also:
    - hivemind.workers.roles.bounded_loop.sources for RoleSources, this class's one base and the
      canonical home of its logic.
    - hivemind.workers.roles.drone for Drone, this class's one builder.
"""

from __future__ import annotations

from hivemind.workers.roles.bounded_loop.sources import ROLE_NOTES_LIMIT, RoleSources

DRONE_NOTES_LIMIT = ROLE_NOTES_LIMIT  # This package's own name for the shared limit.

__all__ = ["DRONE_NOTES_LIMIT", "DroneSources"]


class DroneSources(RoleSources):
    """What one Drone attempt knows, answering `hivemind.memory.HotStateSources`.

    Every method is `RoleSources`'s own (module docstring): this subclass exists only so the
    Drone's own name and import path keep working.
    """
