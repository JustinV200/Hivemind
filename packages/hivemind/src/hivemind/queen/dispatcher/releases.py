"""Define the release lane's moves: release a Virtual Cell beside the tick, then collect it.

Releasing a Virtual Cell -- asking its Warden to stop gracefully (`hivemind.queen.cell_gate.
quiesce`, up to 5 s), then tearing the Cell down or pausing it for reuse -- used to run inside the
Queen's tick, from a finished task's result (`hivemind.queen.ticks.results.complete_task`) and
from a dispatch pass releasing a Cell its task will never use (`hivemind.queen.dispatcher.
provisions.release_cell`). For those seconds her inbox, her Warden liveness and the Hive
Entrance's goals all waited (`.claude/phase-10-handoff.md`, "Still open after the patches").
Provisioning moved beside the tick first; this module moves release the same way:
`start_release` hands the Cell to `QueenDeps.on_task_finished` (the release chain,
`hivemind.queen.cell_gate.release.make_on_task_finished`) as an asyncio task the lane owns, at
most `ReleaseLane.limit` running at once, and returns. While it runs, placement offers that Cell
to no task (`hivemind.queen.dispatcher.snapshot`): before, the release had always finished before
the next dispatch pass looked. When it ends it sets the Queen's wake signal, so a task waiting for
the room it frees is placed on her next tick, and `collect_releases` (every dispatch pass) reads
how it ended. `stop_releases` awaits whatever is still running as the Queen stops and never
cancels it, so the Hive's shutdown (`hivemind.queen.cell_gate.shutdown`) retires every Cell left
afterwards without racing a release half done.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called by `hivemind.queen.ticks.results` and `hivemind.queen.dispatcher.
    provisions` (start_release), `hivemind.queen.dispatcher.ready` (collect_releases) and
    `hivemind.queen.queen` as she stops (stop_releases). Calls into `hivemind.brood_chamber`
    (TaskOutcome), `hivemind.common.logging`, `hivemind.hive` (HiveError), `hivemind.queen.deps`
    (QueenDeps, ReleaseLane) and waggle only.

Key invariants:
    - No tick awaits a release while the Queen runs: it starts one, or reads one that has ended.
    - At most `ReleaseLane.limit` releases run at once; one past that waits its turn, never
      dropped.
    - Two releases of one Cell run one after the other, in the order they were asked for.
    - Every release is owned by the lane from its start to its collection, or to `stop_releases`,
      which awaits it; once the lane is closed a release runs inline instead.

See Also:
    - hivemind.queen.deps for ReleaseLane, the book this module keeps.
    - hivemind.queen.dispatcher.provisions for the provisioning lane this one mirrors.
    - hivemind.queen.cell_gate.release for the release chain each release runs.
"""

from __future__ import annotations

import asyncio

from hivemind.brood_chamber import TaskOutcome
from hivemind.common.logging import get_logger
from hivemind.hive import HiveError
from hivemind.queen.deps import QueenDeps, ReleaseLane
from waggle.ids import CellId

__all__ = ["collect_releases", "drain_releases", "start_release", "stop_releases"]

log = get_logger(__name__)


async def start_release(deps: QueenDeps, cell_id: CellId, outcome: TaskOutcome) -> None:
    """Start releasing `cell_id` beside the tick, told `outcome`; inline once the lane is closed.

    Args:
        deps: The Queen's collaborators; `dispatch.releases` is written. A Hive with no release
            chain wired (`deps.on_task_finished` is None) has nothing to release: a no-op.
        cell_id: The Cell whose task ended, or which its task will never use.
        outcome: What the release chain is told: a success may keep the Cell dormant, anything
            else tears it down (`hivemind.queen.cell_gate.release`).
    """
    chain = deps.on_task_finished
    if chain is None:
        return
    lane = deps.dispatch.releases
    if lane.closed:
        # The Queen is stopping: a release she could no longer await would race the shutdown's
        # retirement of every Cell, so it runs now, in whatever asked for it.
        await chain(cell_id, outcome)
        return
    # A release of this Cell still running goes first (two in a row must never interleave).
    previous = lane.jobs.get(cell_id)
    # Owned by the lane (codingrules 11): collected by a later pass, or awaited by stop_releases.
    lane.jobs[cell_id] = asyncio.ensure_future(_release(deps, cell_id, outcome, previous))


async def collect_releases(deps: QueenDeps) -> None:
    """Drop every release that has ended from the lane, logging one that failed.

    A release's own failure (a backend that refused to destroy or pause the Cell) is the
    lifecycle's to record, and the next Queen's reconcile and `hive cells abscond` its backstops;
    it never ends the Queen's loop.

    Args:
        deps: The Queen's collaborators; ended entries leave `dispatch.releases.jobs`.

    Raises:
        Exception: An unexpected failure a release ended in (never a `HiveError`), surfaced to
            the tick exactly as an unexpected provisioning failure is.
    """
    lane = deps.dispatch.releases
    for cell_id, job in list(lane.jobs.items()):
        if not job.done():
            continue  # Still running: collected on a later pass.
        del lane.jobs[cell_id]
        _raise_unexpected(cell_id, job)


async def drain_releases(deps: QueenDeps) -> None:
    """Await every release still running, then collect them all.

    Args:
        deps: The Queen's collaborators.

    Raises:
        Exception: As `collect_releases`.
    """
    jobs = list(deps.dispatch.releases.jobs.values())
    if jobs:
        # Each release is bounded by its own quiesce timeout and the backend's teardown.
        await asyncio.wait(jobs)
    await collect_releases(deps)


async def stop_releases(deps: QueenDeps) -> None:
    """Close the lane, then await every release still running; never cancel one.

    Args:
        deps: The Queen's collaborators; `dispatch.releases` is closed and emptied.
    """
    lane = deps.dispatch.releases
    lane.closed = True
    jobs = list(lane.jobs.values())
    lane.jobs.clear()
    if not jobs:
        return
    await asyncio.wait(jobs)
    for job in jobs:
        error = None if job.cancelled() else job.exception()
        if error is not None and not isinstance(error, HiveError):
            # Nothing is left to hand it to as the Queen stops, so it is logged, never swallowed.
            log.warning("queen.release_failed_at_stop", error=type(error).__name__)


async def _release(
    deps: QueenDeps, cell_id: CellId, outcome: TaskOutcome, previous: asyncio.Task[None] | None
) -> None:
    """Run one release once its turn comes, then wake the tick so it is collected at once."""
    try:
        if previous is not None:
            await asyncio.wait([previous])
        async with _slots(deps.dispatch.releases):
            chain = deps.on_task_finished
            if chain is not None:
                await chain(cell_id, outcome)
    finally:
        # The room this release frees may be what a PENDING task waits for.
        deps.wake.set()


def _slots(lane: ReleaseLane) -> asyncio.Semaphore:
    """Return the lane's bound on releases running at once, built inside the running loop."""
    if lane.slots is None:
        lane.slots = asyncio.Semaphore(lane.limit)
    return lane.slots


def _raise_unexpected(cell_id: CellId, job: asyncio.Task[None]) -> None:
    """Log a release that failed as the backend's own refusal; raise any other failure."""
    if job.cancelled():
        return  # Never by the Queen (she awaits, never cancels): nothing to report.
    error = job.exception()
    if error is None:
        return
    if isinstance(error, HiveError):
        log.warning("queen.release_failed", cell_id=cell_id, error=type(error).__name__)
        return
    raise error
