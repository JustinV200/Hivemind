"""Define open_lease: a Warden leases its own Cell, once the lease_creation point allows it.

`hivemind.wardens.warden.Warden.start` delegates here (the kernel file sits at its size cap). A
Warden leases the one Cell its source reports, opens its session and computes its own capability
set from the lease and the Cell's running-display opt-in (`hivemind.guard.warden_set`, ADR-0031);
with no Cell, or a lease the source refuses, it moves to WATCH instead and never raises
(codingrules section 8.8: the Hive Stand's Warden exists whenever the Queen runs). Roadmap step
10.3 (ADR-0039) puts the `lease_creation` enforcement point in front of the lease: the capability
this Warden's Cell needs (`WardenDeps.lease_capability`, set by the composition root that built
the source: `cell:hive_stand`, `cell:virtual`, ...) must be allowed by the `warden` role's default
through the Guard's `Enforcer`, with the Cell's own tier and access level as the context; a
refusal is a `guard.denied` row and a WATCH, exactly like a lease the source itself refused.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why these read and write its private state). Calls into
    `hivemind.cell` (Cell, LeaseRefusedError, LeaseRequest), `hivemind.guard`,
    `hivemind.pheromone` (WardenEvent), `hivemind.wardens.state` and waggle only.

Key invariants:
    - The source's `lease` is never called unless the `lease_creation` check allowed it: a Warden
      whose set does not hold its Cell's lease capability touches nothing on the Cell.
    - Every path records exactly the events the old `start()` did (`warden.watch`, or
      `warden.started` then `warden.active`), plus the Enforcer's own `guard.denied` on a refusal.
    - The held set is the `warden` role's default without its `{scratch}` entries: no lease, so no
      scratch root, exists yet, and no lease capability is a scratch-scoped one.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for lease_creation.
    - hivemind.wardens.deps for WardenDeps.lease_capability and WardenDeps.enforcer.
    - hivemind.wardens.warden for Warden.start, this function's one caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import Cell, LeaseRefusedError, LeaseRequest
from hivemind.guard import (
    WARDEN_ROLE,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    warden_principal,
    warden_set,
)
from hivemind.pheromone import WardenEvent
from hivemind.wardens.state import WardenState, assert_transition
from waggle.ids import new_event_id

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["open_lease"]


async def open_lease(warden: Warden) -> None:
    """Lease `warden`'s Cell and open its session, or move it to WATCH when it may not or cannot.

    Args:
        warden: The Warden starting up (read and written directly; see the module docstring).
    """
    cells = await warden._deps.source.cells()
    # No Cell to lease, or the Guard refused this Warden's lease capability: watch instead.
    if not cells or not await _may_lease(warden, cells[0]):
        await _watch(warden)
        return
    cell = cells[0]  # v0: one Warden, one Cell.
    request = LeaseRequest(
        cell_id=cell.id,
        holder=warden._warden_id,
        task_id=None,
        access_level=cell.access_level,
        allowed_paths=(),
    )
    try:
        lease = await warden._deps.source.lease(request)
    except LeaseRefusedError:
        await _watch(warden)
        return
    warden._lease = lease
    warden._cell = cell
    warden._session = await warden._deps.source.open_session(lease)
    # The operator's real-display opt-in rides on the Cell's own report (roadmap step 6.3).
    warden._ceiling = warden_set(
        warden._deps.guard,
        lease.access_level,
        lease.scratch_root,
        real_display=cell.capabilities.real_display_allowed,
    )
    assert_transition(warden._state, WardenState.ACTIVE, warden_id=warden._warden_id)
    warden._state = WardenState.ACTIVE
    await _record(warden, "warden.started")
    await _record(warden, "warden.active")


async def _may_lease(warden: Warden, cell: Cell) -> bool:
    """Pass the `lease_creation` point for `cell`; a refusal is already on the trail."""
    deps = warden._deps
    request = PolicyRequest(
        principal=warden_principal(warden._warden_id),
        point=EnforcementPoint.LEASE_CREATION,
        needed=deps.lease_capability,
        held=deps.guard.roles[WARDEN_ROLE].allow,
        context=PolicyContext(comb_shield=cell.comb_shield, access_level=cell.access_level),
    )
    return (await deps.enforcer.check(request)).allowed


async def _watch(warden: Warden) -> None:
    """Settle in WATCH with no lease, and say so on the trail (the old `start()`'s own path)."""
    warden._state = WardenState.WATCH
    await _record(warden, "warden.watch")


async def _record(warden: Warden, kind: str) -> None:
    """Record one warden.* event about `warden`, the same shape `warden.py`'s own writer uses."""
    deps = warden._deps
    event = WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=kind,
        subject_id=warden._warden_id,
        payload={},
    )
    await deps.trail.record(event)
