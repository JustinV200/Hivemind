"""Start and stop the Queen's tool jobs: the long work that runs beside her tick, never inside it.

A promotion flow waits on a model and a sandbox, a Basket shipment waits for acknowledgements and
a sandbox request waits for a Warden's report: seconds to minutes each. Awaited in her tick they
would hold every Warden's Heartbeat unread (codingrules 8.8: autopilot never awaits a model, long
work runs beside the tick). So each is an asyncio task owned by the `ToolLane` from its start to its
end: `start_job` for work that answers one tool request (at most one per tool id),
`start_background` for work nobody asked for by id (an announcement, a queued shipment). A
finished job wakes the tick and logs a failure it did not handle itself; `stop_tools` cancels
whatever is left and awaits it, so a sandbox a cancelled job held is released by that job's own
`finally` before the Queen returns.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by the request, quarantine, announce and drain handlers, and by `hivemind.queen.queen` as she
    stops. Calls into `hivemind.queen.deps` (ToolLane) only.

Key invariants:
    - Every task is held by the lane until it ends, so none is ever a bare, unowned task
      (codingrules section 11).
    - No job starts once the lane is closed, and a second job for a tool id already in flight
      is refused (the caller answers its requester).
    - A cancelled job is awaited, never abandoned: its `finally` blocks run before `stop_tools`
      returns.

See Also:
    - hivemind.queen.dispatcher.provisions for the same lane shape around Virtual Cells.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine

from hivemind.common.logging import get_logger
from hivemind.queen.deps import QueenDeps
from hivemind.queen.errors import SandboxUnavailableError
from waggle.ids import ToolId, WardenId

log = get_logger(__name__)

__all__ = ["forget_warden", "in_flight", "start_background", "start_job", "stop_tools"]


def in_flight(deps: QueenDeps, tool_id: ToolId) -> bool:
    """Return whether a job for `tool_id` is running right now."""
    job = deps.tool_lane.jobs.get(tool_id)
    return job is not None and not job.done()


def start_job(deps: QueenDeps, tool_id: ToolId, work: Coroutine[None, None, None]) -> bool:
    """Run `work` as the one job for `tool_id`, beside the tick, when the lane has room.

    Args:
        deps: The Queen's collaborators; `tool_lane.jobs` is written.
        tool_id: The tool the job serves.
        work: The coroutine; it must answer its own requester and never raise for a refusal.

    Returns:
        True once started. False (and `work` closed unawaited) when the lane is closed or full or
        a job for `tool_id` already runs; the caller refuses the request.
    """
    lane = deps.tool_lane
    busy = sum(1 for job in lane.jobs.values() if not job.done())
    if lane.closed or busy >= lane.limit or in_flight(deps, tool_id):
        work.close()  # Never started: closing it silences the "never awaited" warning.
        return False
    job = asyncio.ensure_future(work)
    lane.jobs[tool_id] = job
    job.add_done_callback(lambda done: _finished(deps, done, tool_id))
    return True


def start_background(deps: QueenDeps, work: Coroutine[None, None, None]) -> bool:
    """Run `work` as a job nobody asked for by id (an announcement, a queued shipment).

    Args:
        deps: The Queen's collaborators; `tool_lane.background` is written.
        work: The coroutine; it must handle its own failures.

    Returns:
        True once started; False (and `work` closed unawaited) when the lane is closed.
    """
    lane = deps.tool_lane
    if lane.closed:
        work.close()
        return False
    job = asyncio.ensure_future(work)
    lane.background.add(job)
    job.add_done_callback(lambda done: _finished(deps, done, None))
    return True


async def stop_tools(deps: QueenDeps) -> None:
    """Close the lane, cancel every job still running and await them all.

    Args:
        deps: The Queen's collaborators; `tool_lane` is closed and emptied.
    """
    lane = deps.tool_lane
    lane.closed = True
    jobs = [*lane.jobs.values(), *lane.background]
    for job in jobs:
        job.cancel()
    if jobs:
        # Cancelled, not waited out: a job may be minutes from a verdict, and the Queen stopping
        # is the reason nobody will read it. Each job's own `finally` releases what it held.
        await asyncio.gather(*jobs, return_exceptions=True)
    lane.jobs.clear()
    lane.background.clear()
    for future in lane.shipped.values():
        if not future.done():
            future.cancel()


def forget_warden(deps: QueenDeps, warden_id: WardenId) -> None:
    """End whatever waits on a Warden that has detached, instead of waiting out its timeout.

    Its uploads in progress are dropped (the spool file stays, so a Warden that comes back resumes),
    its shipments out fail, and a Comb order awaiting its report fails as an unavailable sandbox.

    Args:
        deps: The Queen's collaborators; `tool_lane` is read and emptied of that Warden's entries.
        warden_id: The Warden whose link has gone.
    """
    lane = deps.tool_lane
    for table in (lane.receipts, lane.offers, lane.transfers):
        for key in [key for key in table if key[0] == warden_id]:
            del table[key]
    for key, future in lane.shipped.items():
        if key[0] == warden_id and not future.done():
            future.set_result(False)
    for pending in lane.pending_reports.values():
        if pending.warden_id == warden_id and not pending.future.done():
            pending.future.set_exception(SandboxUnavailableError("the sandbox Cell's Warden left."))


def _finished(deps: QueenDeps, job: asyncio.Task[None], tool_id: ToolId | None) -> None:
    """Drop a finished job from the lane, log an unhandled failure and wake the tick."""
    lane = deps.tool_lane
    if tool_id is not None and lane.jobs.get(tool_id) is job:
        del lane.jobs[tool_id]
    lane.background.discard(job)
    if not job.cancelled() and job.exception() is not None:
        # A handler is meant to answer its own failures; one that escaped is a bug worth seeing.
        log.error("queen.tools.job_failed", error=repr(job.exception()))
    deps.wake.set()
