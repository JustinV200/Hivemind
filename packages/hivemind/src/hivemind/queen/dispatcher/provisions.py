"""Define the provisioning lane's moves: acquire a Virtual Cell beside the tick, then collect it.

Provisioning a Virtual Cell -- a container or VM booting, its Warden dialling back and proving
itself -- takes seconds to minutes. The dispatch pass used to await it, and so did the Queen's
tick around it: a slow provision held her inbox, her Warden liveness and the Hive Entrance's goals
for as long as it took (a real Docker run, 2026-09-24: 5.6 s against a 3 s liveness window). A
Virtual placement now starts an acquisition here instead (`start_provision`: `hivemind.queen.
dispatcher.acquire.acquire_virtual`, its ADR-0028 retry included, as an asyncio task the lane
owns), at most `ProvisionLane.limit` at once, and the pass moves on; the task stays PENDING and
every pass skips it while its Cell is being made (`provisioning`). The acquisition sets the Queen's
wake signal as it ends, so her next tick collects it (`acquired`): a Cell whose task still waits
for it is placed through the same grant-then-assign path as any other (an acquisition that failed
even its retry is recorded as a placement failure, and a later pass places the task afresh), and
one its task can no longer use is released (`release_cell`) -- the task was cancelled or failed
meanwhile (`settle_provisions`), or its grant was denied once it was placed (`hivemind.queen.
dispatcher.ready`). A Cell acquired for a task whose grant must wait stays with that task here, so
the next pass places the same Cell rather than provisioning another. `stop_provisions` awaits
whatever is still in flight when the Queen stops and never cancels it: a provision cut short could
leave a container its backend never cleans up, while one allowed to finish is a Cell the lifecycle
tracks, which the Hive's shutdown retires with every other.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.dispatcher.ready` on every dispatch pass and by
    `hivemind.queen.queen` as she stops (stop_provisions). Calls into `hivemind.brood_chamber`
    (Task, TaskOutcome, TaskStatus), `hivemind.common.logging`, `hivemind.hive`
    (CellProvisionError), `hivemind.queen.deps` (ProvisionJob, QueenDeps, WardenLink),
    `hivemind.queen.dispatcher.acquire` (acquire_virtual), `hivemind.queen.placement`
    (Placement, PlacementError, ProvisionVirtual, ReuseDormant), `hivemind.queen.trail`
    (record_event) and waggle only.

Key invariants:
    - No dispatch pass awaits an acquisition: it starts one, or reads one that has ended.
    - At most `ProvisionLane.limit` acquisitions are in flight at once, and none starts once the
      Queen has begun to stop.
    - A Cell acquired for a task is placed for that task or released, never left behind; a Cell
      the task did not have acquired for it (an attached Cell a failed provision's retry fell
      back to) is never released here.
    - Every acquisition is owned by the lane from its start to its collection, or to
      `stop_provisions`, which awaits it.

See Also:
    - hivemind.queen.deps for ProvisionLane and ProvisionJob, the book this module keeps.
    - hivemind.queen.dispatcher.acquire for acquire_virtual, what each acquisition runs.
    - hivemind.queen.cell_gate.release for the release chain `release_cell` hands a Cell to.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Sequence

from hivemind.brood_chamber import Task, TaskOutcome, TaskStatus
from hivemind.common.logging import get_logger
from hivemind.hive import CellProvisionError
from hivemind.queen.deps import ProvisionJob, QueenDeps, WardenLink
from hivemind.queen.dispatcher.acquire import acquire_virtual
from hivemind.queen.placement import Placement, PlacementError, ProvisionVirtual, ReuseDormant
from hivemind.queen.trail import record_event
from waggle.ids import TaskId

# Failures an acquisition may end in that say nothing new once its task is gone: the lifecycle
# already recorded a failed provision, and a refusal only mattered while the task could run.
_MOOT_FOR_A_GONE_TASK: tuple[type[Exception], ...] = (CellProvisionError, PlacementError)
# Why a Cell is released, as the `queen.decided` row names it: ids and enum values only.
TASK_GONE = "task_gone"
GRANT_DENIED = "grant_denied"

log = get_logger(__name__)

__all__ = [
    "GRANT_DENIED",
    "TASK_GONE",
    "acquired",
    "acquired_for_task",
    "forget_provision",
    "provisioning",
    "release_cell",
    "settle_provisions",
    "start_provision",
    "stop_provisions",
]


def start_provision(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    task: Task,
    placement: ProvisionVirtual | ReuseDormant,
) -> bool:
    """Start acquiring `placement`'s Cell for `task` beside the tick, when the lane has room.

    Args:
        deps: The Queen's collaborators; `dispatch.provisions` is written.
        wardens: Every attached Warden, for a retry that falls back to an attached Cell.
        task: The ready task the Cell is for; it stays PENDING meanwhile.
        placement: A Virtual Placement `hivemind.queen.dispatcher.acquire.authorize_virtual`
            already passed.

    Returns:
        True when the acquisition started; False when `limit` are already in flight or the Queen
        is stopping, and the task waits PENDING for a later pass.
    """
    lane = deps.dispatch.provisions
    in_flight = sum(1 for held in lane.jobs.values() if not held.job.done())
    if lane.closed or in_flight >= lane.limit:
        return False
    # Owned by the lane (codingrules 11): collected by a later pass, or awaited by stop_provisions;
    # never a bare task whose handle is dropped.
    job = asyncio.ensure_future(_acquire(deps, tuple(wardens), task, placement))
    lane.jobs[task.id] = ProvisionJob(placement=placement, job=job)
    return True


async def _acquire(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    task: Task,
    placement: ProvisionVirtual | ReuseDormant,
) -> tuple[WardenLink, Placement]:
    """Acquire one Cell, then wake the tick so it collects the Cell (or the failure) at once."""
    try:
        return await acquire_virtual(deps, wardens, task, placement)
    finally:
        # Without this the Cell would wait for the next Heartbeat to wake her before it is used.
        deps.wake.set()


def provisioning(deps: QueenDeps, task_id: TaskId) -> bool:
    """Return whether a Cell is being acquired for `task_id` right now."""
    held = deps.dispatch.provisions.jobs.get(task_id)
    return held is not None and not held.job.done()


def acquired(deps: QueenDeps, task_id: TaskId) -> tuple[WardenLink, Placement] | None:
    """Return the Cell acquired for `task_id`, or None when none has been (or it was cut short).

    Args:
        deps: The Queen's collaborators.
        task_id: A ready task no acquisition is in flight for (`provisioning` is False).

    Returns:
        The acquired Cell's link and the Placement actually used; the Cell stays with the task in
        the lane until it is placed (`forget_provision`) or released.

    Raises:
        PlacementError: The acquisition was refused or found no Cell, or failed twice in a row
            (a `CellProvisionError`, whose message this carries); its entry is dropped first, so
            the failure is recorded on this pass and a later pass places the task afresh.
    """
    lane = deps.dispatch.provisions
    held = lane.jobs.get(task_id)
    if held is None or not held.job.done():
        return None
    if held.job.cancelled():
        # Never by the Queen (she awaits, never cancels): nothing was acquired, and its
        # cancellation is not this pass's own to raise, so the task is simply placed afresh.
        del lane.jobs[task_id]
        return None
    if held.job.exception() is not None:
        del lane.jobs[task_id]  # Nothing was acquired; the result below raises why.
    try:
        return held.job.result()
    except CellProvisionError as exc:
        # Collected beside the tick, a provision that failed even its retry is one more passing
        # placement failure (the backend may recover), never an error that ends the Queen's loop.
        raise PlacementError(str(exc)) from exc


def forget_provision(deps: QueenDeps, task_id: TaskId) -> None:
    """Drop `task_id`'s acquired Cell from the lane: it is the task's own now, or released."""
    deps.dispatch.provisions.jobs.pop(task_id, None)


def acquired_for_task(placement: Placement) -> bool:
    """Return whether `placement` made or resumed a Cell for its task alone, not an attached one."""
    return isinstance(placement, ProvisionVirtual | ReuseDormant)


async def settle_provisions(deps: QueenDeps, ready: Collection[TaskId]) -> None:
    """Release every acquired Cell whose task no longer waits for it, and drop its entry.

    A task cancelled or failed while its Cell was being made leaves an entry whose task is not
    among `ready`; one still in flight is left until it ends, and settled on a later pass.

    Args:
        deps: The Queen's collaborators.
        ready: The ids of every task ready to dispatch on this pass.

    Raises:
        Exception: An unexpected failure an acquisition ended in (never a failed provision or a
            refusal, which say nothing new once the task is gone), surfaced to the tick.
    """
    lane = deps.dispatch.provisions
    for task_id, held in list(lane.jobs.items()):
        if task_id in ready or not held.job.done():
            continue  # Still wanted, or still being made: placed or settled on a later pass.
        del lane.jobs[task_id]
        placed = _outcome_for_gone_task(held)
        if placed is not None and acquired_for_task(placed[1]):
            await release_cell(deps, await deps.chamber.get(task_id), placed[0], TASK_GONE)


def _outcome_for_gone_task(held: ProvisionJob) -> tuple[WardenLink, Placement] | None:
    """Return a finished acquisition's Cell, None for a moot failure; raise an unexpected one."""
    if held.job.cancelled():
        return None
    error = held.job.exception()
    if error is None:
        return held.job.result()
    if isinstance(error, _MOOT_FOR_A_GONE_TASK):
        return None
    raise error


async def release_cell(deps: QueenDeps, task: Task, link: WardenLink, cause: str) -> None:
    """Release a Cell acquired for `task` that the task will never run on: tear it down.

    Handed to the same release chain a finished task's Cell takes (`QueenDeps.on_task_finished`),
    with the task's own outcome -- never a success, so the Cell is torn down rather than kept
    dormant for reuse -- and a no-op for any Cell the lifecycle does not track.

    Args:
        deps: The Queen's collaborators.
        task: The task the Cell was acquired for, as it stands now.
        link: The Cell's link.
        cause: `TASK_GONE` or `GRANT_DENIED`, for the `queen.decided` row.
    """
    await record_event(
        deps, "queen.decided", task.id, reason="cell_released", cause=cause, cell_id=link.cell.id
    )
    if deps.on_task_finished is None:
        return  # No release chain wired (a Hive with no Virtual side): nothing to tear down.
    await deps.on_task_finished(link.cell.id, _released_outcome(task, cause))


def _released_outcome(task: Task, cause: str) -> TaskOutcome:
    """Return the outcome a released Cell's chain is told: the task's own, else a cancellation."""
    if task.outcome is not None and task.outcome.status is not TaskStatus.SUCCEEDED:
        return task.outcome
    return TaskOutcome(status=TaskStatus.CANCELLED, summary=f"Its Cell was released: {cause}.")


async def stop_provisions(deps: QueenDeps) -> None:
    """Close the lane, then await every acquisition still in flight; its task stays PENDING.

    Awaited, never cancelled (module docstring): each ends within its own ready timeout, and the
    Cell it made, tracked by the lifecycle, is retired at shutdown with every other Cell.

    Args:
        deps: The Queen's collaborators; `dispatch.provisions` is closed and emptied.
    """
    lane = deps.dispatch.provisions
    # Under the pass lock: a pass already running finishes first, and none after it starts more.
    async with deps.dispatch.lock:
        lane.closed = True
        jobs = [held.job for held in lane.jobs.values()]
        lane.jobs.clear()
    if not jobs:
        return
    await asyncio.wait(jobs)
    for job in jobs:
        error = None if job.cancelled() else job.exception()
        if error is not None and not isinstance(error, _MOOT_FOR_A_GONE_TASK):
            # Nothing is left to hand it to as the Queen stops, so it is logged, never swallowed.
            log.warning("queen.provision_failed_at_stop", error=type(error).__name__)
