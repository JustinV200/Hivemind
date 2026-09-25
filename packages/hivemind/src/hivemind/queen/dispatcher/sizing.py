"""Define SizedGrant and size_grant: every grant the dispatcher sends, and the limits sizing it.

Every grant the Queen's dispatcher sends -- a first dispatch, a retry, a resume -- is sized here,
by `hivemind.forage.allocate.grant` (the pure Forage allocator). A fresh dispatch reads the Cell's
capacity as it stands right now when its link can (`WardenLink.live_capacity`: the Hive Stand
re-reads its one-minute load and its free memory on every call); otherwise, and always for a retry
or a resume, the grant is sized from the attached link's own Cell, as probed (a Virtual Cell's
figures are its spec, fixed for its whole life). Before this module every grant read the link's
Cell, probed once when the Hive was built, so a load that had dropped since was never seen. A
retry or a resume keeps that reading on purpose: a RUNNING task has no PENDING queue to wait in,
so a live reading that zeroed its grant would only fail running work on a busy moment, the very
defect a fresh dispatch's wait removes. `SizedGrant` keeps the allocator's own limits
(`hivemind.forage.SubBeeLimits`: each of the five limits, now and at best) beside the grant, and
whether the reading was live, so `hivemind.queen.dispatcher.zero_grant` can tell a grant a passing
shortfall zeroed from one no wait could ever lift. `size_for_capacity` sizes the grant a fresh
Virtual Cell would get before any is made, from the capacity its spec promises (the very figures
the Cell reports once running: its reservation's), so a Cell is never provisioned for a task only
for its grant to be denied there.

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
    - `SizedGrant.is_live` is True only when the caller asked for a live reading and the link
      carries a reader for its Cell (`WardenLink.live_capacity`); it is never read off the Cell's
      kind (codingrules 8.7).
    - A grant `size_for_capacity` sizes is never recorded or sent: no Cell or Warden exists yet,
      so its ids are placeholders minted for the computation alone.

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
from waggle.ids import CellId, WardenId, new_cell_id, new_grant_id, new_warden_id
from waggle.messages.task import WorkerRole

__all__ = ["SizedGrant", "size_for_capacity", "size_grant"]


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


async def size_grant(
    deps: QueenDeps, link: WardenLink, task: Task, *, read_live: bool = True
) -> SizedGrant:
    """Size a fresh grant for `task` on `link`'s Cell, from the Cell's live figures when asked.

    Args:
        deps: The Queen's collaborators.
        link: The Warden the grant is for, the Cell it runs, and that Cell's live reader if any.
        task: The task whose tempo and goal caps size the grant.
        read_live: Read the Cell's capacity as it stands through the link's reader (a fresh
            dispatch, which can wait out a passing shortfall); False sizes it from the link's own
            Cell as probed (a retry or a resume, which cannot wait; module docstring).

    Returns:
        The grant, its inputs, its limits and whether its capacity reading was live.
    """
    reader = link.live_capacity if read_live else None
    # Normally a handful of fast system reads on this host (the Hive Stand's load average, free
    # memory and free disk); None means no live reading, so the link's own Cell is the figure.
    live = await reader() if reader is not None else None
    capacity = live if live is not None else link.cell.capacity
    inputs = _grant_inputs(deps, capacity, task, (link.warden_id, link.cell.id))
    return SizedGrant(
        inputs=inputs,
        grant=grant(inputs),
        limits=sub_bee_limits(inputs),
        is_live=live is not None,
    )


def size_for_capacity(deps: QueenDeps, capacity: ForageCapacity, task: Task) -> SizedGrant:
    """Size the grant a Cell promising `capacity` would get for `task`, before the Cell exists.

    Args:
        deps: The Queen's collaborators.
        capacity: The capacity a fresh Virtual Cell's spec promises, fixed for its whole life.
        task: The task whose tempo and goal caps size the grant.

    Returns:
        The grant, its inputs and its limits, never live: nothing re-reads a spec.
    """
    # Placeholders minted for this computation alone (module docstring): no Warden or Cell yet.
    placeholder = (new_warden_id(deps.clock), new_cell_id(deps.clock))
    inputs = _grant_inputs(deps, capacity, task, placeholder)
    return SizedGrant(
        inputs=inputs, grant=grant(inputs), limits=sub_bee_limits(inputs), is_live=False
    )


def _grant_inputs(
    deps: QueenDeps, capacity: ForageCapacity, task: Task, holder: tuple[WardenId, CellId]
) -> GrantInputs:
    """Build one task's GrantInputs from its Cell's reading, its Tempo and its goal's budgets."""
    warden_id, cell_id = holder
    return GrantInputs(
        cell_capacity=capacity,
        role=WorkerRole.DRONE,
        footprint=deps.footprints[WorkerRole.DRONE],
        tempo=task.spec.needs.tempo,
        map=deps.map,
        reserve=deps.reserve,
        # Roadmap step 10.5: a requested goal's own budget narrows the manifest's per-goal cap.
        budgets=goal_budgets(deps.budgets, task.spec),
        holder=warden_id,
        cell_id=cell_id,
        task_id=task.id,
        grant_id=new_grant_id(deps.clock),
        now=deps.clock.now(),
        ttl_s=deps.grant_ttl_s,
    )
