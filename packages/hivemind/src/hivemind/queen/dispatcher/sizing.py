"""Define SizedGrant and size_grant: a fresh grant, sized from its Cell's figures as they stand.

Every grant the Queen's dispatcher sends -- a first dispatch, a retry, a resume -- is sized here,
by `hivemind.forage.allocate.grant` (the pure Forage allocator), from the Cell's capacity as it
stands right now when its link can read one (`WardenLink.live_capacity`: the Hive Stand re-reads
its one-minute load and its free memory on every call), and from the attached link's own Cell
otherwise (a Virtual Cell, whose resources its spec fixes for its whole life). Before this module
the dispatcher always read the link's Cell, probed once when the Hive was built, so a load that
had dropped since was never seen and one that had risen was never noticed. `SizedGrant` keeps the
allocator's own limits (`hivemind.forage.SubBeeLimits`: each of the five limits, now and at best)
beside the grant, and whether the reading was live, so `hivemind.queen.dispatcher.zero_grant` can
tell a grant a passing shortfall zeroed from one no wait could ever lift.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready` for every fresh grant; read by
    `hivemind.queen.dispatcher.zero_grant`. Calls into `hivemind.brood_chamber` (Task),
    `hivemind.forage` (ForageCapacity, ForageGrant, GrantInputs, SubBeeLimits, grant,
    sub_bee_limits), `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.intake`
    (goal_budgets) and waggle only.

Key invariants:
    - `size_grant` records nothing and sends nothing: a task that then waits has left no trace
      but the one wait event `zero_grant` writes.
    - `SizedGrant.grant.max_sub_bees == SizedGrant.limits.max_sub_bees`: both come from the same
      inputs through the allocator's one computation.
    - `SizedGrant.is_live` is True only when the link carries a live reader for its Cell
      (`WardenLink.live_capacity`); it is never read off the Cell's kind (codingrules 8.7).

See Also:
    - hivemind.forage.allocate for grant and sub_bee_limits, the pure computation this wraps.
    - hivemind.queen.deps for WardenLink.live_capacity, the seam the reading comes through.
    - hivemind.queen.dispatcher.zero_grant for what a grant that runs no bee does next.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.brood_chamber import Task
from hivemind.forage import (
    ForageCapacity,
    ForageGrant,
    GrantInputs,
    SubBeeLimits,
    grant,
    sub_bee_limits,
)
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.intake import goal_budgets
from waggle.ids import new_grant_id
from waggle.messages.task import WorkerRole

__all__ = ["SizedGrant", "size_grant"]


@dataclass(frozen=True, slots=True)
class SizedGrant:
    """A fresh grant, the inputs it was sized from, and the limits that sized it.

    Attributes:
        inputs: Everything the allocator read, the Cell's capacity reading included.
        grant: The grant the allocator computed (`hivemind.queen.dispatcher.grants.
            authorize_grant` may still narrow its bindings before it is sent).
        limits: Each of the five limits on its sub-bees, now and at best.
        is_live: Whether the capacity reading is refreshed live, so a shortfall in its free
            figures can pass while its task waits.
        waited_s: How long its task had waited for a grant when that wait ran out of patience;
            None for a grant no task waited for.
    """

    inputs: GrantInputs
    grant: ForageGrant
    limits: SubBeeLimits
    is_live: bool
    waited_s: float | None = None


async def size_grant(deps: QueenDeps, link: WardenLink, task: Task) -> SizedGrant:
    """Size a fresh grant for `task` on `link`'s Cell, from the Cell's live figures when known.

    Args:
        deps: The Queen's collaborators.
        link: The Warden the grant is for, the Cell it runs, and that Cell's live reader if any.
        task: The task whose tempo and goal caps size the grant.

    Returns:
        The grant, its inputs, its limits and whether its capacity reading was live.
    """
    # Normally a handful of fast system reads on this host (the Hive Stand's load average, free
    # memory and free disk); None means the Cell has no live reading, so its link's own Cell is
    # the best figure there is.
    live = await link.live_capacity() if link.live_capacity is not None else None
    capacity = live if live is not None else link.cell.capacity
    inputs = _grant_inputs(deps, link, capacity, task)
    return SizedGrant(
        inputs=inputs,
        grant=grant(inputs),
        limits=sub_bee_limits(inputs),
        is_live=live is not None,
    )


def _grant_inputs(
    deps: QueenDeps, link: WardenLink, capacity: ForageCapacity, task: Task
) -> GrantInputs:
    """Build one task's GrantInputs from its Cell's reading, its Tempo and its goal's budgets."""
    return GrantInputs(
        cell_capacity=capacity,
        role=WorkerRole.DRONE,
        footprint=deps.footprints[WorkerRole.DRONE],
        tempo=task.spec.needs.tempo,
        map=deps.map,
        reserve=deps.reserve,
        # Roadmap step 10.5: a requested goal's own budget narrows the manifest's per-goal cap.
        budgets=goal_budgets(deps.budgets, task.spec),
        holder=link.warden_id,
        cell_id=link.cell.id,
        task_id=task.id,
        grant_id=new_grant_id(deps.clock),
        now=deps.clock.now(),
        ttl_s=deps.grant_ttl_s,
    )
