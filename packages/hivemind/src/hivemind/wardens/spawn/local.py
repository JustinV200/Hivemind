"""Define LocalAssignment and spawn_local: start a sub-bee the Warden asked for itself.

Every sub-bee a Warden (the always-on supervisor of one Cell, a unit of compute) spawns serves a
task the Queen (the Hive's central orchestrator) assigned, under a grant she issued, and reports
back to her. One kind does not: a Warden that is cut off from her and holds a model of its own
spawns a short-lived Drone to find out why (roadmap 11.9a, ADR-0056), with nobody to ask. Its task
was minted on the Warden, under a grant carved from the Warden's own local pool, and the Queen has
never heard of either. `spawn_local` is the whole of the plumbing that needs: it starts the bee
through the ordinary `spawn_sub_bee`, so the bee is bound through the Guard's `slot_binding` point,
capped by the Cell's access level and gated by Capping like any other, and it marks the row as the
Warden's own (`SubBee.local`) so that the tick handlers keep the bee's ending on the row instead of
accepting it, retrying it or sending it up. It does one thing more: it offers the bee no Comb
tools, so a bee the Warden started for itself never reaches the Royal Jelly Lab or the Quarantine
Comb, which exist to promote tools for other bees.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's spawn
    sub-package. Called by `hivemind.wardens.offline.diagnose`, which owns the slot the bee takes
    and registers the row it returns. Calls into `hivemind.wardens.spawn.spawn` and `.sub_bee` and
    waggle only.

Key invariants:
    - The binding is explicit: the caller names the `[llm.slots]` key, so routing never picks a
      hosted model for a bee that must run on the Warden's own pool, and the Guard still checks
      the key against the grant and the bee's set.
    - The returned row has `local` set before anything can arrive from the bee; nothing here
      registers it with the Warden or takes a slot for it.
    - The bee is offered no Comb tools, whatever the Warden holds.

See Also:
    - docs/adr/0056-active-reconnection-diagnostic-drones.md for why the Warden asks for itself.
    - hivemind.wardens.spawn.spawn for `spawn_sub_bee`, which does the starting.
    - hivemind.wardens.ticks.results and `.alarms` for where a local row's ending is kept.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from hivemind.wardens.spawn.spawn import WardenCellContext, spawn_sub_bee
from hivemind.wardens.spawn.sub_bee import LocalRun, SubBee
from waggle.messages.forage import GrantIssued
from waggle.messages.task import TaskAssign

__all__ = ["LocalAssignment", "spawn_local"]


@dataclass(frozen=True, slots=True)
class LocalAssignment:
    """A task the Warden minted for itself, and what it runs under.

    Attributes:
        assignment: The task, with an id minted on the Warden.
        grant: The grant carved from the Warden's own local pool; never sent anywhere and never
            added to the Warden's table of the Queen's grants.
        binding: The `[llm.slots]` key the bee's model is bound to.
    """

    assignment: TaskAssign
    grant: GrantIssued
    binding: str


async def spawn_local(ctx: WardenCellContext, wanted: LocalAssignment) -> SubBee:
    """Start the sub-bee for `wanted` on `ctx`'s Cell, marked as the Warden's own.

    Args:
        ctx: The Warden and the Cell it owns, as for any spawn.
        wanted: The task, its grant and the binding to run it on.

    Returns:
        The sub-bee's row, its runtime started and its `local` set. The caller takes the slot it
        occupies before calling and registers the row after.

    Raises:
        hivemind.wardens.errors.BindingRefusedError: The Guard refused the binding; nothing was
            started and `guard.denied` is already on the trail.
    """
    # No Comb: the bee is not given the Warden's Royal Jelly tools to author or promote anything.
    own = dataclasses.replace(ctx, comb=None)
    sub_bee = await spawn_sub_bee(own, wanted.assignment, wanted.grant, wanted.binding)
    sub_bee.local = LocalRun()
    return sub_bee
