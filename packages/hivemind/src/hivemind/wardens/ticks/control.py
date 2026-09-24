"""Forward TaskCancel, TaskPause, TaskResume and Intervene from the Queen to the right sub-bee.

Roadmap step 3.19's own dispatch map: "TaskCancel/TaskPause/TaskResume/Intervene from the Queen ->
FORWARD_CONTROL to the sub-bee running that task." `forward_control` is that whole rule for three
of the four: TaskCancel, TaskPause and TaskResume are relayed unchanged, over the sub-bee's own
link, with a fresh envelope addressed by this Warden -- none of them carries a reply the Warden
itself needs to wait for or interpret. A Queen-sent `Intervene(REBIND)` is the one exception (this
dispatch's own fix 3c): relaying it unchanged only ever makes the sub-bee checkpoint and stop
(`hivemind.workers.runtime.loop.WorkerRuntime._handle_intervene`'s own Rebind handling), and
nothing ever spawned a fresh one afterwards, so the goal always finished on the sub-bee's original
binding. `_handle_queen_rebind` is what actually rebinds: it reads the target `[llm.slots]`
manifest key straight off `Intervene.binding` (the Queen already resolved it,
`hivemind.queen.ticks.alarms._fallback_binding_key`, and fills the field before sending) and calls
`hivemind.wardens.ticks.alarms.rebind_sub_bee`, the same retire-and-respawn-on-an-explicit-key
primitive a sub-bee's own locally-decided REBIND uses once it has picked a target. `handle_stop`
(roadmap step 5.3 / ADR-0027) is the fourth control lever, and the only one that is not a relay:
a Queen-sent `Shutdown` or `CellTeardownRequest` maps to `WardenAction.STOP` and ends this Warden
through its own `stop()`, rather than falling through to an awake episode as both used to.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. A `hivemind.wardens.warden.Warden` own delegate (see `hivemind.wardens.ticks.
    assign`'s own module docstring for why). Calls into `hivemind.wardens.ticks.alarms`
    (rebind_sub_bee), `hivemind.wardens.state` (clustering_update), `hivemind.wardens.spawn`
    (stop_sub_bee), `hivemind.wardens.snapshot_relay` (RelaySnapshotter), `hivemind.supervision.
    attendant` (InboxItem) and waggle only.

Key invariants:
    - `forward_control` relays nothing, but still runs its own Clustering bookkeeping, when
      `sub_bee` is None (the Queen named a task this Warden no longer has a sub-bee running, e.g.
      it already finished): a stale control message from the Queen is not this module's contract
      to enforce.
    - A Queen-sent `Intervene` reaching this Warden is always from the Queen link (a sub-bee never
      sends one to its own Warden), so no further sender check is needed before treating a REBIND
      action as this module's own rebind path rather than a plain relay.
    - `_handle_queen_rebind` is a no-op, not an error, when `Intervene.binding` is unset: the Queen
      always fills it before sending REBIND (`hivemind.queen.ticks.alarms._rebind` only sends the
      message once it has resolved a fallback key), so an unset field only ever means a peer that
      skipped that step, not something this Warden should guess at.

See Also:
    - .claude/roadmap.md step 3.19's own dispatch map for the FORWARD_CONTROL rule.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for the in-Cell
      Warden `handle_stop` ends when the Queen orders a teardown.
    - waggle.messages.control.protocol for Shutdown and waggle.messages.cell.leases for
      CellTeardownRequest, the two messages `handle_stop` answers.
    - waggle.messages.task for TaskCancel, TaskPause and TaskResume.
    - waggle.messages.supervision for Intervene and InterventionAction.
    - hivemind.wardens.ticks.alarms for rebind_sub_bee, this module's one rebind primitive.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap
from hivemind.forage import Ceilings, HostingPlan, SlotPlan, SourceChain
from hivemind.supervision import Intervention, to_wire
from hivemind.supervision.attendant import InboxItem
from hivemind.wardens.errors import UnknownSubBeeError
from hivemind.wardens.snapshot_relay import RelaySnapshotter
from hivemind.wardens.spawn import stop_sub_bee
from hivemind.wardens.state import clustering_update
from hivemind.wardens.ticks.alarms import rebind_sub_bee
from waggle.envelope import Hop, wrap
from waggle.ids import WorkerId
from waggle.messages.cell import LeaseReleased, ReleaseCause
from waggle.messages.cell.snapshot import CellRollbackReply, CellSnapshotReply
from waggle.messages.forage import CeilingsSet, PlanWritten
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskCancel, TaskPause, TaskResume

if TYPE_CHECKING:
    from hivemind.cell import LeaseReleaseReport, RealCellLease
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

__all__ = [
    "forward_control",
    "handle_ceilings_set",
    "handle_plan_written",
    "handle_release_lease",
    "handle_snapshot_reply",
    "handle_stop",
    "send_intervention",
]

_Control = TaskCancel | TaskPause | TaskResume | Intervene
_QUEEN_LINK = "queen"  # Mirrors hivemind.wardens.warden's own InboxItem.principal key.

_LOG = get_logger(__name__)


async def handle_stop(warden: Warden, payload: object) -> None:
    """Carry out the Queen's own Shutdown or CellTeardownRequest: stop this Warden for good.

    `WardenAction.STOP`'s one handler (`hivemind.wardens.autopilot.table.decide`). There is nothing
    to decide and nothing to relay: `hivemind.wardens.warden.Warden.stop` already sets the tick
    loop's own stop flag first, then stops every sub-bee cooperatively, reaps every receive task
    and releases the lease, which is exactly what both messages ask for. For the Warden running
    inside the Cell a `CellTeardownRequest` names (ADR-0027), "this Cell is about to be destroyed"
    and "stop" are the same order; for a Warden on the Hive Stand the Queen only ever sends the
    teardown for a Cell it is about to destroy anyway, so the same response holds.

    Nothing extra is recorded on the trail here: `stop()` itself writes `warden.stopped`, which is
    the audited fact (codingrules section 12); which of the two messages asked for it is a
    debugging detail, so it goes in the log line below rather than into the trail vocabulary.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        payload: The `Shutdown` or `CellTeardownRequest` that ordered this; logged, not relayed.
    """
    _LOG.info("warden.stopping", warden_id=warden._warden_id, ordered_by=type(payload).__name__)
    await warden.stop()


async def forward_control(
    warden: Warden, item: InboxItem, sub_bee: SubBee | None, payload: _Control
) -> None:
    """Relay `payload` to `sub_bee`, or actually carry out a Queen-sent Intervene(REBIND).

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        item: The InboxItem `payload` came from; `item.principal`/`.task_id` decide the roadmap
            step 4.9 (Clustering) ACTIVE <-> CLUSTERED bookkeeping below.
        sub_bee: The sub-bee `payload` concerns; a no-op when None (module docstring).
        payload: The control message to relay: TaskCancel, TaskPause, TaskResume or Intervene.
    """
    # Roadmap step 4.9 (Clustering): read by `hivemind.wardens.ticks.assign.settle_after_tick`'s
    # own ACTIVE <-> CLUSTERED move; `clustering_update`'s own docstring explains the decision.
    if item.principal == _QUEEN_LINK:
        clustering_update(item.task_id, payload, warden._clustered_tasks)
    if sub_bee is None:
        return
    if isinstance(payload, Intervene) and payload.action is InterventionAction.REBIND:
        await _handle_queen_rebind(warden, sub_bee, payload)
        return
    hop = Hop(
        sender=warden._warden_id, recipient=sub_bee.worker_id, node_id=warden._deps.identity.node_id
    )
    await sub_bee.link.send(wrap(payload, hop, clock=warden._deps.clock))


async def _handle_queen_rebind(warden: Warden, sub_bee: SubBee, intervene: Intervene) -> None:
    """Turn the Queen's own Intervene(REBIND) into a real rebind respawn (module docstring)."""
    if intervene.binding is None:
        return  # Defensive: the Queen always fills this before sending REBIND (module docstring).
    await rebind_sub_bee(warden, sub_bee, intervene.binding)


def handle_ceilings_set(warden: Warden, payload: CeilingsSet) -> None:
    """Store the Queen's own Ceilings and resize this Warden's own sub-bee slot pool to match.

    Roadmap step 4.8's own wiring step: nothing here records a fresh trail event (the Queen's own
    `hivemind.queen.forage.ceilings.set_ceilings`/`change_ceilings` already recorded
    `forage.ceilings_set` before sending this). `SubBeeSlots` resizes to the smaller of what the
    Warden's own standing grant already allows and what these ceilings now allow -- a ceiling is a
    bound on top of a grant, never a grant of its own (codingrules section 8.10: "ceilings, not
    approvals").

    Args:
        warden: The owning Warden (read and written directly; see this package's own module
            docstring for why these are plain functions, not Warden methods).
        payload: The Queen's own CeilingsSet.
    """
    ceilings = Ceilings.from_wire(payload.ceilings)
    warden._ceilings = ceilings
    warden._sub_bee_slots.resize(min(warden._sub_bee_slots.capacity, ceilings.max_sub_bees))


async def send_intervention(warden: Warden, child: str, intervention: Intervention) -> None:
    """Send `intervention` to `child` over its own link -- `Warden.intervene`'s own delegate.

    Args:
        warden: The owning Warden (read directly; see the module docstring).
        child: The sub-bee's own worker id, as `hivemind.supervision.supervisor.Supervisor`
            names it.
        intervention: What to do and why.

    Raises:
        UnknownSubBeeError: `child` names no current sub-bee.
    """
    sub_bee = warden._sub_bees.get(WorkerId(child))
    if sub_bee is None:
        raise UnknownSubBeeError(child)
    action, slot = to_wire(intervention)
    message = Intervene(
        action=action,
        subject=None,
        task_id=sub_bee.task_id,
        slot=slot,
        alarm_id=None,
        reason=intervention.reason,
    )
    hop = Hop(
        sender=warden._warden_id, recipient=sub_bee.worker_id, node_id=warden._deps.hop.node_id
    )
    await sub_bee.link.send(wrap(message, hop, clock=warden._deps.clock))


def handle_plan_written(warden: Warden, payload: PlanWritten) -> None:
    """Store the Queen's own HostingPlan for this Warden's Cell.

    Nothing here records a fresh trail event: `hivemind.queen.forage.hosting.write_hosting_plan`
    already recorded `forage.plan_written` before sending this (module docstring of
    `handle_ceilings_set`, the same reasoning).

    Args:
        warden: The owning Warden.
        payload: The Queen's own PlanWritten.
    """
    warden._hosting_plan = HostingPlan(
        cell_id=payload.cell_id,
        revision=payload.revision,
        slots=tuple(SlotPlan.from_wire(slot) for slot in payload.slots),
        default=SourceChain.from_wire(payload.default),
        reason=payload.reason,
    )


async def handle_release_lease(warden: Warden, payload: Intervene) -> None:
    """Carry out a Queen-sent Intervene(RELEASE_LEASE): stop every sub-bee, release the lease.

    Roadmap step 5.13's own missing lever (`hivemind.queen.cluster.tick.run_release_tick`):
    unlike `handle_stop` (ADR-0027's Shutdown/CellTeardownRequest, which ends this whole Warden),
    this Warden's own Cell is not being destroyed here -- only its lease. `settle_after_tick`
    (called right after every `hivemind.wardens.ticks.dispatch.act`, `hivemind.wardens.ticks.
    assign`'s own module docstring) settles this Warden back to WATCH on its own once
    `_sub_bees` is empty, so this handler never touches `warden._state` itself. Idempotent:
    `RealCellLease.release()` is idempotent, and a Warden already lease-less (WATCH since
    `start()` was refused) simply has nothing to release or report.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        payload: The Queen's own Intervene(RELEASE_LEASE); only `.reason` is read.
    """
    for sub_bee in tuple(warden._sub_bees.values()):
        # Cooperative first, cancel-and-reap only as stop_sub_bee's own bounded fallback: never
        # left cancelled-but-unawaited (codingrules section 11), mirroring Warden.stop's own loop.
        await stop_sub_bee(sub_bee, warden._deps.clock)
        receive_task = warden._receive_tasks.pop(sub_bee.worker_id, None)
        if receive_task is not None:
            await reap(receive_task)
        await sub_bee.link.close()
    warden._sub_bees.clear()
    warden._sub_bee_iters.clear()
    lease = warden._lease
    if lease is None:
        return  # Nothing held (already released, or never leased one): idempotent, nothing to
        # report either -- there is no lease_id for a Queen that asked about one this Warden
        # never opened.
    report = await lease.release()
    warden._lease = None
    await _send_lease_released(warden, lease, report, payload.reason)


async def _send_lease_released(
    warden: Warden, lease: RealCellLease, report: LeaseReleaseReport, reason: str
) -> None:
    """Send `cell.lease_released` to the Queen for `lease`'s own outcome."""
    message = LeaseReleased(
        lease_id=lease.id,
        cell_id=lease.cell_id,
        holder=lease.holder,
        cause=ReleaseCause.COMPLETED,
        is_restored=report.is_restored,
        killed_processes=report.killed_processes,
        residual_paths=tuple(str(path) for path in report.residual_paths),
        reason=reason,
    )
    await warden._deps.queen_link.send(wrap(message, warden._deps.hop, clock=warden._deps.clock))


def handle_snapshot_reply(warden: Warden, payload: CellSnapshotReply | CellRollbackReply) -> None:
    """Resolve this Warden's own RelaySnapshotter with a CellSnapshotReply/CellRollbackReply.

    A no-op, not an error, when `WardenDeps.snapshotter` is not a RelaySnapshotter (every Real
    Cell's own Warden, whose snapshotter is the default NoopSnapshotter): only a Virtual Cell's
    own Warden ever sends the request this answers.

    Args:
        warden: The owning Warden (read directly; see the module docstring).
        payload: The Queen's own reply.
    """
    snapshotter = warden._deps.snapshotter
    if isinstance(snapshotter, RelaySnapshotter):
        snapshotter.handle_reply(payload)
