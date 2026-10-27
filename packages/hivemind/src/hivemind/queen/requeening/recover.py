"""Restore a Queen whose process died: her two halves of Requeening, and `queen.requeened`.

Roadmap 13.1 (ADR-0062). Every start of a Queen is a Requeening; a first start, or one after a
clean stop, finds nothing to do. `begin_requeening` runs before her loop starts, with the Hive
Stand's own Warden attached and the Virtual Cell listener already accepting: it puts her in
REQUEENING, reads back what she handled and every open question's wiring (`.handled`), gives back
the Hive Stand leases her dead process left open (`.leases`, from the newest snapshot's fold),
and sorts every live task by where it stands. A task on a Swarm device is the swarm gate's
(ADR-0053) and left alone; one on a Virtual Cell her backends still list waits for its Warden,
which redials her; every other (the Hive Stand's, whose Warden died with her, or a Virtual Cell's
that is gone) is an orphan and is taken back at once (`.orphans`), with its gone Warden's grants.
The grants of tasks that ended are released, and a Virtual Cell still running a task is walked
back to GRANTED in its lifecycle, which her backends could only report READY. `settle_requeening`
runs on her tick until every Warden she waits for is back or the grace has run out; it then takes
back what the missing Wardens held, resumes every Clustering pause from its newest Handoff through
a Warden that came back, records `queen.requeened` (counts only), moves her to RUNNING and writes
her first snapshot.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. `begin_requeening` is called by `hivemind.cli.compose.hive.run`;
    `settle_requeening` once per tick by `hivemind.queen.ticks.housekeeping`. Calls into
    `hivemind.brood_chamber`, `hivemind.hive` (VirtualCellRecord), `hivemind.queen.dispatcher`
    (resume_paused, imported where used), `hivemind.queen.forage` (release_finished),
    `hivemind.queen.trail`, this sub-package's own modules and waggle only.

Key invariants:
    - Nothing is taken back from a Warden she still waits for, nor from a Swarm device's Cell.
    - `queen.requeened` is recorded exactly once per start, with counts and the snapshot's
      instant only: never a Cell, task or Warden id (the Night Veil boundary, ADR-0062).
    - Every write that moves a task in or out of PENDING runs under the dispatch lock.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for every source of truth used here.
    - docs/runbooks/requeening.md for what an operator sees.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.common.logging import get_logger
from hivemind.hive import VirtualCellRecord
from hivemind.queen.forage import release_finished
from hivemind.queen.requeening.book import PendingRecovery, RequeenCounts
from hivemind.queen.requeening.handled import restore_handled
from hivemind.queen.requeening.leases import HiveStandLeases, release_orphans
from hivemind.queen.requeening.orphans import (
    clustering_paused,
    newest_handoff,
    revoke_held_by,
    take_back,
)
from hivemind.queen.requeening.snapshot import current_leases, take_snapshot
from hivemind.queen.trail import record_event
from waggle.ids import CellId, WardenId

if TYPE_CHECKING:
    # Types only: `hivemind.queen.deps` imports this sub-package (`QueenDeps.requeening`), and
    # the Queen imports `hivemind.queen.deps`.
    from hivemind.queen.deps import QueenDeps, WardenLink
    from hivemind.queen.queen import Queen

REQUEENED_KIND = "queen.requeened"  # The one event a Requeening records about itself.
RESUMED_REASON = "Its Warden came back to the restarted Queen; it resumes from its Handoff."
# Every status a task can stand in while a Warden holds it.
_HELD = (TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.PAUSED)

log = get_logger(__name__)

__all__ = [
    "REQUEENED_KIND",
    "RESUMED_REASON",
    "RequeeningStart",
    "begin_requeening",
    "settle_requeening",
]


@dataclass(frozen=True, slots=True)
class RequeeningStart:
    """What the composition root knows at start that the Queen's deps do not.

    Attributes:
        virtual_cells: Every Virtual Cell her backends list now (`hivemind.queen.cell_gate.
            rearm_gate` read them and re-armed the gate with their keys).
        device_cells: Every Swarm device's Cell: their tasks are the swarm gate's to hold.
        hive_stand: The Hive Stand's own source, which gives back an orphaned lease; None for a
            Hive with no Hive Stand to release on.
    """

    virtual_cells: tuple[VirtualCellRecord, ...] = ()
    device_cells: frozenset[CellId] = field(default_factory=frozenset)
    hive_stand: HiveStandLeases | None = None


async def begin_requeening(queen: Queen, start: RequeeningStart) -> None:
    """Run the first half of Requeening, before the Queen's loop starts (module docstring).

    Args:
        queen: The Queen coming up, with the Hive Stand's Warden attached and not yet run.
        start: The Virtual Cells listed, the device Cells, and the Hive Stand's source.
    """
    deps = queen._deps  # The same cross-module reach `hivemind.queen.questions` takes.
    deps.cluster_state.begin_requeening()
    snapshot = await deps.requeening.store.latest_snapshot()
    await restore_handled(queen)
    attached = {link.warden_id for link in queen.wardens}
    counts = RequeenCounts()
    if start.hive_stand is not None:
        leases = await current_leases(deps, snapshot)
        released = await release_orphans(deps, leases, attached, start.hive_stand)
        counts = RequeenCounts(leases_released=released)
    live = frozenset(record.cell_id for record in start.virtual_cells)
    async with deps.dispatch.lock:  # Like every write that moves a task in or out of PENDING.
        tasks = await _held_tasks(deps)
        expected, orphans = _sort(tasks, attached, live, start.device_cells)
        counts = counts.plus(await _take_back(deps, orphans))
        counts = counts.plus(await _release_ended(deps))
    await _regrant(deps, tasks, live)
    deps.requeening.pending = PendingRecovery(
        deadline=deps.clock.now() + timedelta(seconds=deps.requeening.grace_s),
        expected=expected,
        counts=counts,
        snapshot_at=snapshot.taken_at if snapshot is not None else None,
    )
    if not expected:
        await settle_requeening(deps, queen.wardens)  # Nobody to wait for: settle at once.


async def settle_requeening(deps: QueenDeps, wardens: Sequence[WardenLink]) -> RequeenCounts | None:
    """Finish Requeening once no Warden she waits for holds a task unattached, or the grace ends.

    Called once per tick. A Warden she expected counts as back when it is attached now, or when
    it no longer holds a live task (it came back, finished and left); one still holding a task
    unattached when the grace is over is missing, and its tasks are taken back.

    Args:
        deps: The Queen's collaborators; `requeening.pending` is read and cleared.
        wardens: Every Warden attached now.

    Returns:
        Everything this Requeening did, once it settles; None while it still waits (or when no
        Requeening is under way).
    """
    pending = deps.requeening.pending
    if pending is None:
        return None
    attached = {link.warden_id for link in wardens}
    missing = await _missing(deps, pending.expected, attached)
    if missing and deps.clock.now() < pending.deadline:
        return None  # Still inside the grace, with a Warden yet to redial for a live task.
    async with deps.dispatch.lock:
        held = [task for task in await _held_tasks(deps) if task.warden_id in missing]
        counts = pending.counts.plus(await _take_back(deps, held))
    back = pending.expected - missing
    resumed = await _resume_paused(deps, wardens, back & attached)
    counts = counts.plus(
        RequeenCounts(paused_resumed=resumed, wardens_back=len(back), wardens_missing=len(missing))
    )
    await _settled(deps, counts, pending)
    await take_snapshot(deps, wardens)  # Her next restart folds the trail from here.
    return counts


async def _missing(
    deps: QueenDeps, expected: frozenset[WardenId], attached: Collection[WardenId]
) -> frozenset[WardenId]:
    """Return the expected Wardens not attached that still hold a live task.

    A Warden that came back, finished what it held and left again (its Cell released) is back
    all the same: nothing is waiting on it.
    """
    held = await _held_tasks(deps)
    holding = {task.warden_id for task in held if task.warden_id in expected}
    return frozenset(warden for warden in holding if warden is not None and warden not in attached)


def _sort(
    tasks: Sequence[Task],
    attached: Collection[WardenId],
    live: Collection[CellId],
    devices: Collection[CellId],
) -> tuple[frozenset[WardenId], list[Task]]:
    """Return the Wardens to wait for, and the tasks whose Warden can never come back."""
    expected: set[WardenId] = set()
    orphans: list[Task] = []
    for task in tasks:
        if task.warden_id is None or task.warden_id in attached or task.cell_id in devices:
            continue  # Unplaced, its Warden is here, or a device's: the swarm gate holds those.
        if task.cell_id in live and task.status is not TaskStatus.ASSIGNED:
            expected.add(task.warden_id)  # Its Cell outlived her, and so may its Warden.
        else:
            orphans.append(task)  # The Hive Stand's, a gone Cell's, or stranded ASSIGNED.
    return frozenset(expected), orphans


async def _take_back(deps: QueenDeps, tasks: Sequence[Task]) -> RequeenCounts:
    """Take back the placed tasks and the Clustering pauses among `tasks`; revoke their grants."""
    placed = [task for task in tasks if task.status is not TaskStatus.PAUSED]
    paused = await clustering_paused(deps, [t for t in tasks if t.status is TaskStatus.PAUSED])
    taken = [*placed, *paused]
    counts = await take_back(deps, taken)
    gone = {task.warden_id for task in taken if task.warden_id is not None}
    return counts.plus(RequeenCounts(grants_revoked=await revoke_held_by(deps, gone)))


async def _release_ended(deps: QueenDeps) -> RequeenCounts:
    """Return to the pool every live grant whose task has ended (the ledger outlived them)."""
    tasks = await deps.chamber.list(TaskFilter(limit=MAX_TASK_FILTER_LIMIT))
    ended = {task.id for task in tasks if is_terminal(task.status)}
    released = await release_finished(deps.ledger, deps, ended)
    return RequeenCounts(grants_released=len(released))


async def _regrant(deps: QueenDeps, tasks: Sequence[Task], live: Collection[CellId]) -> None:
    """Walk each Virtual Cell still running a task back to GRANTED in its lifecycle.

    Its backend could only say READY; the Cell is in fact granted to its task, and the release
    that follows the task's end walks GRANTED -> RELEASED, which READY has no edge for.
    """
    if deps.on_cell_granted is None:
        return  # No Virtual side wired: no lifecycle to tell.
    for task in tasks:
        if task.cell_id not in live or task.status is TaskStatus.ASSIGNED:
            continue
        grant = next((g for g in deps.ledger.live_grants() if g.task_id == task.id), None)
        if grant is not None and task.cell_id is not None:
            await deps.on_cell_granted(task.cell_id, grant.id)


async def _resume_paused(
    deps: QueenDeps, wardens: Sequence[WardenLink], back: Collection[WardenId]
) -> int:
    """Resume, through its Warden, every Clustering pause on a Warden that came back."""
    # Local, not module-level: the dispatcher imports `hivemind.queen.deps`, which imports this
    # sub-package, so a module-level import here would be a cycle.
    from hivemind.queen.dispatcher import resume_paused

    paused = await deps.chamber.list(
        TaskFilter(status=TaskStatus.PAUSED, limit=MAX_TASK_FILTER_LIMIT)
    )
    mine = [task for task in paused if task.warden_id in back]
    resumed = 0
    for task in await clustering_paused(deps, mine):
        ref = await newest_handoff(deps, task)
        await resume_paused(deps, wardens, task.id, ref, RESUMED_REASON)
        resumed += 1
    return resumed


async def _held_tasks(deps: QueenDeps) -> list[Task]:
    """Return every task a Warden holds: ASSIGNED, RUNNING, BLOCKED or PAUSED."""
    found: list[Task] = []
    for status in _HELD:
        # Latency: one local indexed read of the Brood Chamber per status.
        found.extend(
            await deps.chamber.list(TaskFilter(status=status, limit=MAX_TASK_FILTER_LIMIT))
        )
    return found


async def _settled(deps: QueenDeps, counts: RequeenCounts, pending: PendingRecovery) -> None:
    """Record `queen.requeened`, move her to RUNNING and end the Requeening."""
    snapshot_at = pending.snapshot_at.isoformat() if pending.snapshot_at is not None else None
    # Counts and one instant only (module invariant): nothing here names a Cell, task or Warden.
    await record_event(
        deps, REQUEENED_KIND, deps.identity.hive_id, snapshot_at=snapshot_at, **asdict(counts)
    )
    deps.cluster_state.finish_requeening()
    deps.requeening.pending = None
    log.info("queen.requeened", **asdict(counts))
