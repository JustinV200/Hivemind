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

A Swarm device's Warden (`WardenDeps.device`, roadmap step 11.6) differs in three ways, all here.
It gives its lease back when it settles to WATCH with nothing left to supervise
(`release_idle_lease`; the next TaskAssign leases again), because a borrowed machine keeps no Hive
scratch between tasks (ADR-0054). It tells the Queen once per loss when its Cell's link drops
(`report_cell_lost`), and holds off that idle release while the link is down, since a release
nothing can confirm only orphans the lease. And a session that cannot open right after the lease
did (the device went away between the two) is a WATCH, not a crash (`open_lease`). A device
Warden with an `Outage` (`hivemind.wardens.offline`) also wakes its tick on each loss and each
return, so that its running sub-bees are paused at a Handoff for the loss and started again on the
return, and the idle release waits for any task it has paused. `release_lease` is the one way a
lease is given back, shared with the offline return, which leases the device again.

Key invariants:
    - The source's `lease` is never called unless the `lease_creation` check allowed it: a Warden
      whose set does not hold its Cell's lease capability touches nothing on the Cell.
    - A lease is dropped from `warden._lease` only once `release()` confirmed it; one that could
      not be released stays there (ORPHANED), so the next idle release or `stop()` retries it.
    - Every path records exactly the events the old `start()` did (`warden.watch`, or
      `warden.started` then `warden.active`), plus the Enforcer's own `guard.denied` on a refusal.
    - The held set is the `warden` role's default without its `{scratch}` entries: no lease, so no
      scratch root, exists yet, and no lease capability is a scratch-scoped one.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for lease_creation.
    - hivemind.wardens.deps.DeviceDeps for the switch that turns the idle release on.
    - hivemind.wardens.deps for WardenDeps.lease_capability and WardenDeps.enforcer.
    - hivemind.wardens.warden for Warden.start, this function's one caller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import Cell, CellError, LeaseRefusedError, LeaseRequest, RealCellLease
from hivemind.common.logging import get_logger
from hivemind.guard import (
    WARDEN_ROLE,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    warden_principal,
    warden_set,
)
from hivemind.pheromone import WardenEvent
from hivemind.wardens.deps import device_outage
from hivemind.wardens.state import WardenState, assert_transition
from hivemind.wardens.ticks.alarms import send_alarm_to_queen
from waggle.errors import TransportError
from waggle.ids import new_event_id
from waggle.messages.forage import SourceRemovalCause
from waggle.messages.supervision import AlarmKind

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

log = get_logger(__name__)

# What a release can fail with when the Cell it reaches is not answering: a device error, a link
# that went, or the machine's own I/O. Anything else is a real bug and still propagates.
RELEASE_FAILURES = (CellError, TransportError, OSError)

__all__ = [
    "RELEASE_FAILURES",
    "clear_cell_lost",
    "open_lease",
    "release_idle_lease",
    "release_lease",
    "report_cell_lost",
]


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
    try:
        warden._session = await warden._deps.source.open_session(lease)
    except CellError as error:
        # A device that answered the lease and went away before the session opened: nothing can
        # run on it, so give the lease back and wait in WATCH like a refused lease does, instead
        # of ending this Warden's run loop with an exception.
        log.warning("warden.session_unavailable", lease_id=lease.id, error=type(error).__name__)
        await _give_back(warden, lease)
        await _watch(warden)
        return
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


async def release_lease(warden: Warden, lease: RealCellLease) -> None:
    """Stop what `lease` hosts, then give it back; it stays held, ORPHANED, if the Cell is silent.

    Args:
        warden: The owning Warden, whose lease this drops once the Cell confirmed the release.
        lease: The lease to give back (normally `warden._lease`).
    """
    # A model server dies with the lease that started it, so the Hive stops seeing it now.
    if warden._models is not None:
        await warden._models.stop_all(SourceRemovalCause.LEASE_RELEASED)
    await _give_back(warden, lease)


async def _give_back(warden: Warden, lease: RealCellLease) -> None:
    """Release `lease` and forget it, or keep it for a retry when the Cell cannot confirm it."""
    try:
        await lease.release()
    except RELEASE_FAILURES as error:
        # ORPHANED (`RealCellLease.release`): `warden._lease` keeps it, so an idle release or
        # `stop()` tries again once the Cell can answer; forgetting it here would leave it for good.
        log.warning("warden.release_unconfirmed", lease_id=lease.id, error=type(error).__name__)
        return
    warden._lease = None


async def release_idle_lease(warden: Warden) -> None:
    """Give a device Warden's lease back once it has settled to WATCH with nothing to supervise.

    Called at the end of every tick by `hivemind.wardens.ticks.assign.settle_after_tick`; it does
    nothing unless `WardenDeps.device` asks for it and the Warden is idle, so a Hive Stand's or a
    Virtual Cell's Warden is untouched. Idle means WATCH, no sub-bee, no assignment parked for
    a grant that is still to arrive (that one is about to start, so the lease stays), no job the
    Warden owns in flight and no model server running (a Nuc keeps its Cell while it hosts one).
    The next TaskAssign finds no lease and leases again (`hivemind.wardens.ticks.assign`'s own
    retry). A release the device cannot confirm leaves the lease ORPHANED and held; the next tick
    retries.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
    """
    device, lease = warden._deps.device, warden._lease
    if device is None or not device.release_lease_when_idle or lease is None:
        return
    # While the link is down a release only orphans the lease, so wait for `clear_cell_lost`.
    if warden._state is not WardenState.WATCH or warden._cell_lost:
        return
    if warden._sub_bees or warden._pending:
        return
    # A job in flight (a promotion starting this device's model server, a sandbox run) works on
    # this very lease, and a model server that runs dies with it: neither is idle work.
    if warden._tool_jobs or (warden._models is not None and warden._models.running):
        return
    # Tasks an outage paused resume on this very lease once the link is back (`offline.resume`).
    outage = device_outage(warden._deps)
    if outage is not None and outage.has_held:
        return
    await release_lease(warden, lease)


async def report_cell_lost(warden: Warden, reason: str) -> None:
    """Tell the Queen this Warden's Cell link dropped, once, however often the loss is reported.

    The device's Warden runs on the Hive Stand, so unlike a Warden that went silent it keeps
    heartbeating while its device is away: the Queen would never learn from liveness. The lease is
    treated as lost from here until `clear_cell_lost`: kept, because the device's dead-man switch
    or its return settles it, but not released (`release_idle_lease`). What happens to the tasks
    on the device is the Queen's to decide, not this Warden's.

    Args:
        warden: The owning Warden.
        reason: Why the link is gone (a closed link, missed heartbeats); a short sentence.
    """
    if warden._cell_lost:
        return  # Already told for this loss.
    # Set before the first await, so two reports of one loss can never both pass the check above.
    warden._cell_lost = True
    _wake(warden)  # The tick, not this caller's task, pauses the sub-bees (`offline.reconcile`).
    await send_alarm_to_queen(
        warden,
        kind=AlarmKind.CELL_UNREACHABLE,
        detail=f"The link to this Warden's Cell was lost: {reason}",
        reason="A Cell's link is not this Warden's to restore; the Queen decides what its tasks "
        "do while the device is away.",
    )


def clear_cell_lost(warden: Warden) -> None:
    """Note that this Warden's Cell link is back, so the next loss is reported afresh.

    Args:
        warden: The owning Warden.
    """
    warden._cell_lost = False
    _wake(warden)  # Tasks paused for the loss resume on the tick this wakes.


def _wake(warden: Warden) -> None:
    """Wake `warden`'s tick if it has an outage to look at; the call may come from any task."""
    outage = device_outage(warden._deps)
    if outage is not None:
        outage.nudge()


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
