"""Drive a cut-off Warden's search for why its link went, one notch per tick.

`investigate` is the one call the Warden's tick makes about the search (roadmap 11.9a, ADR-0056),
made by `reconcile` right after it has looked at the link, and it is level-triggered like the rest
of the offline package: it reads the facts, asks `decide_move` what is due, does that, and asks
again until nothing is. So a tick that finds a Drone has just claimed a result collects it,
sees the link is still down, and begins waiting for it to come back, all in one pass; and a tick
that finds nothing due returns at once. A search begins with the outage (the first tick that finds
the link down) and belongs to it: it sends a Drone if the Warden has a model of its own and a
free slot, collects what the Drone reports, sends another if the first found no cause and attempts
and time remain, and finishes when the link is back, a cause is known and has had its chance to
bring the link back, or the budget is spent. It does not stop when the link returns: a Drone that
fixed it is given a moment to write up what it did, because that is the finding. Finishing records
`warden.diagnosed` and deposits the findings as Nectar, which the link queues if it is still down.
A Warden with no model of its own sends nothing and records nothing.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    offline.diagnose sub-package. Called by `hivemind.wardens.offline.resume.reconcile` once per
    tick. Calls into `.decide`, `.drone`, `.report` and `.state`, `hivemind.wardens.offline.outage`
    and nothing else.

Key invariants:
    - Nothing awaits a Drone: it is a sub-bee of the Warden, its frames arrive on the tick like
      any other, and this function only reads what the tick has already put on its row.
    - The search never outlasts `wall_clock_s` from the moment the link went, and the Warden
      clusters fully past `max_offline_s` whatever the search is doing, so the budget is carved
      out of the offline limit and never added to it.
    - A search is finished exactly once, whichever way it ends: a Drone still in flight is retired,
      `warden.diagnosed` is recorded once, and the Nectar is deposited once.
    - A search left over from an earlier outage is finished before a new one begins.

See Also:
    - hivemind.wardens.offline.diagnose.decide for the moves and the verdicts.
    - hivemind.wardens.offline.resume for `reconcile`, which calls this.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from hivemind.wardens.offline.diagnose.decide import (
    Move,
    Phase,
    Snapshot,
    Verdict,
    decide_move,
    decide_verdict,
)
from hivemind.wardens.offline.diagnose.drone import (
    Start,
    collect,
    has_ended,
    start_drone,
    stop_drone,
)
from hivemind.wardens.offline.diagnose.report import (
    Judged,
    deposit_findings,
    findings_document,
    record_verdict,
)
from hivemind.wardens.offline.diagnose.state import Investigation
from hivemind.wardens.offline.outage import Outage, ReconnectBudget, RemoteDeps

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

MAX_MOVES = 4  # More than a tick can need: collect, start, and one more; WAIT ends it sooner.
NO_SLOT_REASON = "no_slot"  # Why a search that wanted a Drone and never had a slot for one ended.

__all__ = ["MAX_MOVES", "NO_SLOT_REASON", "investigate"]


async def investigate(warden: Warden, outage: Outage, remote: RemoteDeps) -> None:
    """Take the search for why the link went as far as this tick's facts allow.

    Args:
        warden: The Warden whose tick is running (read and written directly).
        outage: Its outage, which holds the search.
        remote: Its link, the offline limit, the budget and the Queen's host.
    """
    budget = remote.reconnect_budget
    if budget is None:
        return  # This Warden investigates nothing.
    search = await _search_of(warden, outage, remote)
    if search is None or search.phase is Phase.OVER:
        return
    for _ in range(MAX_MOVES):
        move = decide_move(_snapshot(warden, remote, budget, search))
        if move is Move.WAIT or not await _apply(warden, outage, remote, search, move):
            return


async def _search_of(warden: Warden, outage: Outage, remote: RemoteDeps) -> Investigation | None:
    """Return this outage's search (beginning it if need be), or the one still lingering."""
    search, cut = outage.investigation, outage.cut_since
    if search is not None and cut is not None and search.cut_since != cut:
        # Left over from an outage that ended: its Drone may still be writing; it is done now.
        if search.phase is not Phase.OVER:
            await _finish(warden, outage, remote, search)
        search = outage.investigation = None
    if search is None and cut is not None:
        search = outage.investigation = Investigation(cut_since=cut)
    if search is not None and search.phase is Phase.OVER and cut is None:
        outage.investigation = None  # The link is back and the search is over: nothing to keep.
        return None
    return search


def _snapshot(
    warden: Warden, remote: RemoteDeps, budget: ReconnectBudget, search: Investigation
) -> Snapshot:
    """Read this tick's facts about the search: the clock, the link and the Drone in flight."""
    now = warden._deps.clock.now()
    up = remote.link.is_online
    if up and search.link_up_since is None:
        search.link_up_since = now  # First seen back: the Drone has this long to write up.
    verifying = search.verifying_since
    return Snapshot(
        phase=search.phase,
        attempts=search.attempts,
        budget=budget,
        elapsed_s=(now - search.cut_since).total_seconds(),
        link_up_for_s=_since(now, search.link_up_since) if up else None,
        ended=search.phase is Phase.RUNNING and has_ended(warden, search),
        verifying_s=_since(now, verifying) or 0.0,
    )


def _since(now: datetime, then: datetime | None) -> float | None:
    """Seconds from `then` to `now`, or None when `then` has not happened."""
    return None if then is None else (now - then).total_seconds()


async def _apply(
    warden: Warden, outage: Outage, remote: RemoteDeps, search: Investigation, move: Move
) -> bool:
    """Do `move`; True when the search should be asked again this tick."""
    if move is Move.START:
        return await _start(warden, outage, remote, search)
    if move is Move.COLLECT:
        finding = await collect(warden, search)
        search.findings.append(finding)
        if finding.cause is None:
            search.phase = Phase.IDLE
        else:
            search.phase, search.verifying_since = Phase.VERIFYING, warden._deps.clock.now()
        return True
    await _finish(warden, outage, remote, search)  # STOP and FINISH both end the search.
    return False


async def _start(warden: Warden, outage: Outage, remote: RemoteDeps, search: Investigation) -> bool:
    """Send the next Drone; True when it was sent. Ends the search when none ever can be."""
    started = await start_drone(warden, search, remote)
    if started is Start.STARTED:
        search.phase = Phase.RUNNING
        return True
    if started is Start.NOT_ELIGIBLE:
        # No model of its own, no lease, or the Guard refused: nothing to think with. A search
        # that never sent a Drone ends without a word; one that had sent some is judged as it is.
        await _finish(warden, outage, remote, search)
    else:
        search.starved = True  # The Cell is at its ceiling: look again next tick, until time ends.
    return False


async def _finish(
    warden: Warden, outage: Outage, remote: RemoteDeps, search: Investigation
) -> None:
    """End the search: retire the Drone in flight, judge it, deposit the findings and record it."""
    if search.phase is Phase.RUNNING:
        if has_ended(warden, search):
            search.findings.append(await collect(warden, search))
        else:
            await stop_drone(warden, search)  # It ran out of time: what it wrote is lost.
    search.phase = Phase.OVER
    if outage.cut_since is None:
        outage.investigation = None
    if search.attempts == 0 and not search.starved:
        return  # No Drone was ever wanted that could not be sent: nothing happened to record.
    # The link counts as back if any tick saw it back during this search, even if it has dropped
    # again since: that is a new outage with a search of its own.
    link_up = search.link_up_since is not None
    verdict, reason = decide_verdict(link_up=link_up, cause=search.cause)
    deposited = False
    if search.attempts > 0:
        document = findings_document(verdict, remote.peer, search.findings)
        deposited = await deposit_findings(warden, document)
    else:
        verdict, reason = Verdict.NO_CAUSE, NO_SLOT_REASON
    spent_s = (warden._deps.clock.now() - search.cut_since).total_seconds()
    await record_verdict(warden, Judged(verdict, reason, search.attempts, spent_s, deposited))
