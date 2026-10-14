"""Freeze a Warden when it is cut off: pause what cannot think, and cluster fully past the limit.

A Warden (the always-on supervisor of one Cell, a unit of compute) that has lost its link to the
Queen (the Hive's central orchestrator) keeps working within what it owns and never grows
(codingrules 8.8 and 15, ADR-0053). `enter_offline` is the moment it notices: it moves to OFFLINE,
leaves every shared grant exactly as it is (an expiry that passes while it is cut off is not a
revocation, and nothing renews or drops a grant here; the Queen settles them when she re-sends
them on return), and pauses the sub-bees that have nothing to think with. A sub-bee whose model
binding runs entirely on this machine (a Nuc's) keeps going; one that needs a hosted model is told
to stop at a Handoff, the pause Clustering uses (`Intervene(HANDOFF)`, a Queen-style one: the bee
stops, writes its Handoff and is not started again by this Warden's own context check), and its
task is held in the `Outage` until the Warden can start it again. `cluster_fully` is the other
end: an outage that crosses `[supervision] max_offline_s` pauses everything left and joins the
existing Clustering bookkeeping (`Warden._clustered_tasks`, the CLUSTERED state), because past
that limit the Queen has judged the grant lost and those tasks are hers to resume with a fresh
TaskAssign, as with any clustered task. `cut_cell` is the Level 0 counterpart, for a Warden on the
Hive Stand whose device went away: no state change (it never goes OFFLINE), only the pause, so the
sub-bees' commands stop failing against a Cell that is not there.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Called by `hivemind.wardens.offline.resume.reconcile` from the Warden's
    tick. Calls into `hivemind.pheromone` (WardenEvent), `hivemind.wardens.spawn` (the binding's
    locality), `hivemind.wardens.state`, `hivemind.wardens.links` and waggle only.

Key invariants:
    - Every WardenState change here goes through the transition table (`can_transition`) and is
      followed, in the same call, by its `warden.*` event (codingrules Appendix C, rule 3).
    - Only a sub-bee that is still working, not cancelled and not already paused (by the Queen's
      Clustering or by this outage) is paused, and only once per outage.
    - The pause never writes or drops a grant, a lease or a Handoff: the bee writes its own
      Handoff, and the lease stays as it was.
    - A paused task is held in the `Outage` before its bee is told to stop, so a bee that stops
      at once finds its task already held.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for what keeps running.
    - hivemind.wardens.ticks.control for the Queen's own Clustering pause this mirrors.
    - hivemind.wardens.offline.resume for the way back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.pheromone import WardenEvent
from hivemind.wardens.links import send_guarded
from hivemind.wardens.offline.outage import Outage
from hivemind.wardens.spawn.binding import binding_is_local
from hivemind.wardens.state import SETTLED_EVENT_KINDS, WardenState, can_transition
from hivemind.workers.state import WorkerState
from waggle.envelope import Hop, wrap
from waggle.ids import new_event_id
from waggle.messages.supervision import Intervene, InterventionAction

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

LINK_LOST = "The link to the Queen was lost."  # The reason on the trail and on the log.
OVER_LIMIT = "The link to the Queen stayed down past the offline limit."  # Why it clusters fully.
PAUSE_REASON = "Paused at a Handoff while cut off; the Warden carries on from it."  # To the bee.
# The sub-bee states that are still working, so can be told to stop at a Handoff. A bee that is
# PAUSED is the Queen's to resume, and a terminal one is ending by itself.
_WORKING = frozenset({WorkerState.SPAWNED, WorkerState.RUNNING, WorkerState.HANDING_OFF})

__all__ = [
    "LINK_LOST",
    "OVER_LIMIT",
    "cluster_fully",
    "cut_cell",
    "enter_offline",
    "pause_sub_bees",
    "record_event",
]


async def enter_offline(warden: Warden, outage: Outage) -> None:
    """Note the cut, move to OFFLINE if the machine allows it, and pause what cannot think.

    Args:
        warden: The Warden whose link to the Queen just went (read and written directly).
        outage: Its outage, which starts its clock and holds the paused tasks.
    """
    grants = {grant_id: grant.revision for grant_id, grant in warden._grants.items()}
    outage.begin(warden._deps.clock.now(), LINK_LOST, grants)
    # Only ACTIVE and WATCH have an edge to OFFLINE. A Warden the Queen already clustered is
    # paused as it is, so for it the cut only starts the clock.
    if can_transition(warden._state, WardenState.OFFLINE):
        warden._state = WardenState.OFFLINE
    paused = await pause_sub_bees(warden, outage, spare_local=True)
    await record_event(
        warden, "warden.offline", reason=LINK_LOST, paused=paused, frozen_grants=len(grants)
    )


async def cluster_fully(warden: Warden, outage: Outage) -> None:
    """Pause every sub-bee left and hand the paused tasks to the Queen's Clustering.

    Args:
        warden: The Warden whose outage crossed `max_offline_s` (read and written directly).
        outage: Its outage; the tasks it holds are marked as the Queen's to resume.
    """
    await pause_sub_bees(warden, outage, spare_local=False)
    outage.is_clustered_fully = True
    held = outage.held()
    # The existing Clustering bookkeeping: `settled_state` keeps the Warden CLUSTERED while every
    # task it has is in this set, until the Queen's fresh TaskAssign drops one.
    warden._clustered_tasks.update(task.assignment.task_id for task in held)
    if held and can_transition(warden._state, WardenState.CLUSTERED):
        warden._state = WardenState.CLUSTERED
        await record_event(
            warden, SETTLED_EVENT_KINDS[WardenState.CLUSTERED], reason=OVER_LIMIT, paused=len(held)
        )


async def cut_cell(warden: Warden, outage: Outage) -> None:
    """Pause every working sub-bee: its Cell's link is gone, so its commands would fail.

    Args:
        warden: A Level 0 device Warden whose Cell the swarm gate has reported lost.
        outage: Its outage.
    """
    outage.is_cell_cut = True
    # A device's hands are all on the device, so a bee with a local model has nothing to do either.
    await pause_sub_bees(warden, outage, spare_local=False)


async def pause_sub_bees(warden: Warden, outage: Outage, *, spare_local: bool) -> int:
    """Hold each working sub-bee's task and tell the bee to stop at a Handoff.

    Args:
        warden: The owning Warden (read directly).
        outage: Where the paused tasks are held.
        spare_local: Leave a bee whose model binding runs entirely on this machine going: it can
            still think while cut off (a Nuc's).

    Returns:
        How many sub-bees were told to stop.
    """
    now = warden._deps.clock.now()
    paused = 0
    for sub_bee in tuple(warden._sub_bees.values()):
        if not _may_pause(warden, outage, sub_bee, spare_local=spare_local):
            continue
        # Held first: the bee may stop, and its row be retired, before this loop reaches the next.
        outage.hold(sub_bee.assignment, sub_bee.last_handoff, now)
        # A Queen-style pause (`ticks.control.forward_control`): nothing but this package starts
        # the task again, so the Warden's own successor logic must not.
        sub_bee.handoff_ordered = False
        await _order_handoff(warden, sub_bee)
        paused += 1
    return paused


async def record_event(warden: Warden, kind: str, **payload: JsonValue) -> None:
    """Record one `warden.*` event about `warden` on its own trail.

    Args:
        warden: The Warden the event is about.
        kind: A `WardenEvent` kind, e.g. `warden.offline`.
        **payload: Counts and short reasons only, never text a bee wrote (codingrules 12).
    """
    deps = warden._deps
    event = WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=kind,
        subject_id=warden._warden_id,
        payload=payload,
    )
    await deps.trail.record(event)


def _may_pause(warden: Warden, outage: Outage, sub_bee: SubBee, *, spare_local: bool) -> bool:
    """Whether `sub_bee` should be told to stop: working, not already paused, and cut off."""
    if sub_bee.state not in _WORKING or sub_bee.cancelled or sub_bee.local is not None:
        return False  # A bee the Warden started for itself is not a task the Queen could resume.
    # Paused already, by the Queen's Clustering or by this very outage: it is hers or ours already.
    if sub_bee.task_id in warden._clustered_tasks or outage.is_holding(sub_bee.task_id):
        return False
    return not (spare_local and binding_is_local(sub_bee.binding, warden._deps))


async def _order_handoff(warden: Warden, sub_bee: SubBee) -> None:
    """Send `sub_bee` an Intervene(HANDOFF) over its own link; a bee already gone needs none."""
    message = Intervene(
        action=InterventionAction.HANDOFF,
        subject=None,
        task_id=sub_bee.task_id,
        slot=None,
        alarm_id=None,
        reason=PAUSE_REASON,
    )
    hop = Hop(
        sender=warden._warden_id, recipient=sub_bee.worker_id, node_id=warden._deps.identity.node_id
    )
    await send_guarded(sub_bee.link, wrap(message, hop, clock=warden._deps.clock))
