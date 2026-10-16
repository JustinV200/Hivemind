"""Reconcile a Warden with its link and its Cell: enter an outage, leave it, resume what it paused.

`reconcile` is the one call the Warden's tick makes about outages, after it has handled whatever
arrived (so a frame the Queen sent on returning, such as a cancel of a task she re-placed, is acted
on first). It is level-triggered and idempotent: it looks at the link's state (a Warden that dials
the Queen) and at the Cell's (a Level 0 device Warden, which the swarm gate tells), and does
whatever is out of line. A link that is down and not yet noticed enters the outage
(`hivemind.wardens.offline.freeze`); one that stays down past `max_offline_s` clusters fully; one
that is up again leaves it. Leaving OFFLINE goes to whatever the sub-bees say: ACTIVE for those
that kept going, WATCH if none runs (codingrules Appendix C, "OFFLINE -> WATCH": the link came back
with no sub-bee running). The tasks it paused start again a moment later, and the ordinary settle
makes it ACTIVE then. A Warden that clustered fully stays CLUSTERED, its tasks the Queen's to
resume with a fresh TaskAssign. A Warden that holds a model of its own also searches for why its
link went (`hivemind.wardens.offline.diagnose`, roadmap 11.9a), driven from the same pass; the
search never changes what the pass does about the link, the Cell or the tasks it paused. Having
acted on what is due, the pass also says when it must run again with nothing having arrived
(`Outage.wake_in`): the search's budget, the offline limit and the pause before a resume are
deadlines of the clock, which no frame announces, for a Warden on the device and a Level 0 one
alike. A link that is back also finds the Basket shipments the outage interrupted and offers each
blob again (`hivemind.wardens.ticks.basket.redrive_shipments`): the Queen answers with the byte
she holds, so a shipment the link cut resumes instead of waiting out its timeout.

Resuming a paused task is the same act as any TaskAssign for it: the assignment, with
`resume_from` set to the Handoff its bee wrote, goes back through `handle_assign` and the two gates
a Queen-sent resume passes (quarantine and taint), on the same attempt (the work carries on; it
did not fail). Not at once: for `RESUME_SETTLE_S` after the link returns the Queen is given the
first word, since what she sends first (grants re-sent, a cancel of a task she re-placed meanwhile)
decides whether the task is still this Warden's. For a device whose Cell was lost the Warden first
gives its old lease back and leases again, since the device's dead-man switch may have released
it, and it does so after the same pause, so that the report the device sends of what its switch
did is heard before the Warden's own release can pre-empt it; a refused lease is asked for again
after `RESUME_RETRY_S`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline sub-package. Called once per tick by `hivemind.wardens.warden.Warden`. Calls into
    `hivemind.wardens.offline.diagnose`, `.freeze`, `hivemind.wardens.ticks` (assign, basket,
    lease), `hivemind.wardens.isolation`, `hivemind.wardens.quarantine`, `hivemind.wardens.state`
    and waggle only.

Key invariants:
    - A task is resumed only when nothing is cut off: the link to the Queen is up (or the Warden
      has none), the Cell is reachable and, for a device that came back, a lease is held again.
    - A task leaves the outage's hands the moment it is handed to `handle_assign`, so it is never
      started twice; one whose bee is still stopping waits for the next tick.
    - A task the Queen cancels, supersedes or controls while it is held is dropped from it
      (`hivemind.wardens.ticks.control` and `.assign`): her word wins over the Warden's own.
    - Every WardenState change goes through `assert_transition` with its `warden.*` event.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for what the Queen does on a
      return.
    - hivemind.wardens.offline.freeze for what entering an outage does.
    - hivemind.wardens.ticks.assign for `handle_assign`, which every resume goes through.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from hivemind.wardens.isolation import admit_resume
from hivemind.wardens.offline.diagnose.step import investigate
from hivemind.wardens.offline.freeze import cluster_fully, cut_cell, enter_offline, record_event
from hivemind.wardens.offline.outage import HeldTask, Outage, RemoteDeps, outage_of, remote_of
from hivemind.wardens.quarantine import admit_respawn
from hivemind.wardens.state import (
    SETTLED_EVENT_KINDS,
    WardenState,
    assert_transition,
    settled_state,
)
from hivemind.wardens.ticks.assign import handle_assign
from hivemind.wardens.ticks.basket import redrive_shipments
from hivemind.wardens.ticks.lease import release_lease
from hivemind.workers.state import WorkerState

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

RESUME_RETRY_S = 30.0  # A device that would not lease again is left alone this long between asks.
# After the link is back the Warden waits this long before it starts its paused tasks, so what the
# Queen sends first on a return (grants re-sent, a cancel of a task she re-placed) is handled first.
RESUME_SETTLE_S = 2.0

__all__ = ["RESUME_RETRY_S", "RESUME_SETTLE_S", "leave_offline", "reconcile"]


async def reconcile(warden: Warden) -> None:
    """Bring `warden` in line with its link and its Cell; do nothing if it has no outage.

    Args:
        warden: The Warden whose tick is running (read and written directly).
    """
    outage = outage_of(warden)
    if outage is None or warden._state is WardenState.STOPPED:
        return
    # Cleared before the state is read, so a change that lands meanwhile wakes the next tick.
    outage.acknowledge()
    remote = remote_of(warden)
    search_wait = limit_wait = None
    if remote is not None:
        await _follow_link(warden, outage, remote)
        # After the link is read, so a search for why it went begins in the tick that finds it down.
        search_wait = await investigate(warden, outage, remote)
        limit_wait = _until_limit(warden, outage, remote)
    await _follow_cell(warden, outage)
    await _resume_held(warden, outage)
    # Nothing arrives when a budget runs out, the offline limit passes or the pause before a
    # resume ends, so the tick is told to wake then, rather than at the heartbeat that happens to
    # follow. Last, after everything already due was acted on, so a wake always has work to find.
    outage.wake_in(warden._deps.clock.now(), search_wait, limit_wait, _until_retry(warden, outage))


async def leave_offline(warden: Warden, outage: Outage) -> None:
    """Take `warden` out of the outage: the link to the Queen is back.

    Args:
        warden: The Warden whose link returned (read and written directly).
        outage: Its outage; the tasks it holds stay held for `reconcile` to resume, unless the
            Warden clustered fully, when they pass to the Queen.
    """
    if outage.is_clustered_fully:
        # Past the limit the Queen has judged the grant lost: these tasks wait for her fresh
        # TaskAssign like any clustered task, and their Handoffs stay in this store for it.
        outage.take()
    outage.end()
    # A blob half-shipped when the link went is offered again now the link can carry it; the
    # Queen's answer names the byte she holds, and the shipment carries on from there.
    await redrive_shipments(warden)
    # Not the Cell's retry, but the same pause: paused tasks wait for the Queen to speak first.
    if outage.has_held:
        _pause_resume(warden, outage, RESUME_SETTLE_S)
    if warden._state is not WardenState.OFFLINE:
        return  # CLUSTERED stays so; a Warden that never went OFFLINE has nothing to leave.
    # What the sub-bees it has right now say: ACTIVE for a Nuc's that kept going, WATCH (Appendix
    # C's "OFFLINE -> WATCH") if none runs. The tasks it paused start again a moment later, and
    # the ordinary settle moves it to ACTIVE then.
    ids = (sub_bee.task_id for sub_bee in warden._sub_bees.values())
    target = settled_state(ids, warden._clustered_tasks)
    assert_transition(warden._state, target, warden_id=warden._warden_id)
    warden._state = target
    await record_event(warden, SETTLED_EVENT_KINDS[target])


async def _follow_link(warden: Warden, outage: Outage, remote: RemoteDeps) -> None:
    """Enter, hold or leave the outage according to the link's state."""
    if remote.link.is_online:
        if outage.cut_since is not None:
            await leave_offline(warden, outage)
    elif outage.cut_since is None:
        await enter_offline(warden, outage)
    elif not outage.is_clustered_fully and _outage_s(warden, outage) >= remote.max_offline_s:
        await cluster_fully(warden, outage)


async def _follow_cell(warden: Warden, outage: Outage) -> None:
    """Pause for a Cell the swarm gate reported lost; note it is back once it says so."""
    if warden._cell_lost and not outage.is_cell_cut:
        await cut_cell(warden, outage)
    elif outage.is_cell_cut and not warden._cell_lost:
        outage.is_cell_cut = False
        # The device's dead-man switch may have released the lease while it was away.
        outage.is_lease_stale = True
        # Its report of that was queued while it was away and is sent as the link comes back: the
        # Queen's end hears it first, or the Warden's own release would reach the device before the
        # report did, and the lease would be recorded as released without a word of what was killed.
        _pause_resume(warden, outage, RESUME_SETTLE_S)


def _until_limit(warden: Warden, outage: Outage, remote: RemoteDeps) -> float | None:
    """Return the seconds until the Warden clusters fully; None with no outage or once it has."""
    if outage.cut_since is None or outage.is_clustered_fully:
        return None
    return remote.max_offline_s - _outage_s(warden, outage)


def _until_retry(warden: Warden, outage: Outage) -> float | None:
    """Return the seconds until a held task may be resumed; None when no pause gates one."""
    retry = outage.retry_after
    if retry is None or not outage.has_held:
        return None
    # While something is cut off the pause is not what the held tasks wait for: the link's or
    # the Cell's return wakes the tick itself.
    if outage.cut_since is not None or outage.is_cell_cut or warden._cell_lost:
        return None
    return (retry - warden._deps.clock.now()).total_seconds()


def _outage_s(warden: Warden, outage: Outage) -> float:
    """Return how long the link has been down, in seconds of the Warden's own clock."""
    if outage.cut_since is None:
        return 0.0
    return (warden._deps.clock.now() - outage.cut_since).total_seconds()


async def _resume_held(warden: Warden, outage: Outage) -> None:
    """Start every held task again, once nothing is cut off and a lease is held."""
    if not outage.has_held or not _is_reachable(warden, outage):
        return
    if outage.is_lease_stale and not await _lease_again(warden, outage):
        return
    for held in outage.held():
        await _resume_one(warden, outage, held)


def _is_reachable(warden: Warden, outage: Outage) -> bool:
    """Whether the link to the Queen and the Cell are both up and no retry is being waited out."""
    if outage.cut_since is not None or outage.is_cell_cut or warden._cell_lost:
        return False
    retry = outage.retry_after
    return retry is None or warden._deps.clock.now() >= retry


async def _lease_again(warden: Warden, outage: Outage) -> bool:
    """Give the device's old lease back and lease it again; False, with a pause, if it will not."""
    if not _is_idle(warden):
        return False  # A bee is still stopping: leasing again waits until the Warden is idle.
    lease = warden._lease
    if lease is not None:
        await release_lease(warden, lease)
        if warden._lease is not None:
            return _try_later(warden, outage)  # The device would not confirm the release.
    # The release took time, and the Warden may have been stopped meanwhile (the Hive is going
    # down): a stopped Warden leases nothing, and starting it again is not a legal transition.
    if not _is_idle(warden):
        return False
    await warden.start()
    if warden._lease is None:
        return _try_later(warden, outage)  # Refused, or the device is not answering yet.
    outage.is_lease_stale = False
    return True


def _is_idle(warden: Warden) -> bool:
    """Whether the Warden is in WATCH, read through a call so a later read is not narrowed away."""
    return warden._state is WardenState.WATCH


def _try_later(warden: Warden, outage: Outage) -> bool:
    """Set the pause before the next lease attempt, and report that this one did not work."""
    _pause_resume(warden, outage, RESUME_RETRY_S)
    return False


def _pause_resume(warden: Warden, outage: Outage, seconds: float) -> None:
    """Hold off starting paused tasks until `seconds` of the Warden's clock have passed."""
    outage.retry_after = warden._deps.clock.now() + timedelta(seconds=seconds)


async def _resume_one(warden: Warden, outage: Outage, held: HeldTask) -> None:
    """Start one held task again from its Handoff, unless its bee is still stopping."""
    assignment = held.assignment
    bee = next(
        (row for row in warden._sub_bees.values() if row.task_id == assignment.task_id), None
    )
    if bee is not None:
        if bee.state is WorkerState.FAILED:
            outage.forget(assignment.task_id)  # Its Alarm went to the Queen: the task is hers.
        return  # Otherwise the bee has not stopped yet; its Handoff is noted when it does.
    outage.forget(assignment.task_id)
    # The same attempt carries on from where the bee stopped; the inline Handoff the original
    # assignment may have carried is already in this store, so it is not sent again.
    resumed = assignment.model_copy(
        update={"resume_from": held.handoff or assignment.resume_from, "handoff": None}
    )
    if await admit_respawn(warden, resumed) and await admit_resume(warden, resumed):
        await handle_assign(warden, resumed)
