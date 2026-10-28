"""Take back the tasks of a Warden whose link ended while the Queen runs and never came back.

Roadmap 13.6's chaos suite found it: a Virtual Cell killed mid-task (its container gone, its
Warden with it) left its task RUNNING for as long as the Hive ran. The link's end detaches the
Warden (`hivemind.queen.attach.detach_warden`), which leaves the Brood Chamber alone, because a
Warden that only lost its link keeps working cut off and redials (ADR-0053; a Night Veil Cell's
over Tor), and past `[supervision] max_offline_s` it clusters fully and waits to be resumed
(`hivemind.queen.cluster.returned`). Only a restart took such tasks back (ADR-0062). Now her tick
does too: a Warden she placed work on, holding a task, not attached and on no Cell a hold covers
(a device's are the swarm gate's), is noted away; once it has been away for
`RequeeningBook.lost_after_s` (`max_offline_s` plus the redial grace), it is lost. Its tasks are
taken back to PENDING from their newest Handoff the way a restart takes an orphan back (one on a
Night Veil Cell is cancelled), its grants are revoked, and its Cell is released, which tears a
Virtual Cell down; one that comes back before then is simply forgotten from the note.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. Called once per tick by `hivemind.queen.ticks.housekeeping`. Calls
    into `hivemind.brood_chamber`, `hivemind.common.logging`, `hivemind.queen.dispatcher` (the
    release lane, imported where used), `.orphans`, `.book` and waggle only.

Key invariants:
    - Nothing is taken back while a Requeening settles: that judges its own Wardens.
    - A Warden is lost only after `lost_after_s` away, counted from the first tick that saw it
      away holding a task; one attached again, or holding nothing, leaves the note at once.
    - A PAUSED task is never taken here: its hold is someone's to lift.

See Also:
    - hivemind.queen.requeening.recover for the restart's take-back this mirrors.
    - hivemind.queen.swarm_gate.unreachable for a device's, which holds its Cell instead.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import asdict
from typing import TYPE_CHECKING

from hivemind.brood_chamber import Task, TaskFilter, TaskOutcome, TaskStatus
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.common.logging import get_logger
from hivemind.queen.requeening.book import RequeenCounts
from hivemind.queen.requeening.orphans import revoke_held_by, take_back
from waggle.ids import WardenId

if TYPE_CHECKING:
    # Types only: `hivemind.queen.deps` imports this sub-package (`QueenDeps.requeening`).
    from hivemind.queen.deps import QueenDeps, WardenLink

# Why, on each `task.unassigned`, withdrawn question and revoked grant: plain words, no content.
LOST_REASON = "Its Warden's link ended and it did not come back; the Queen places it afresh."
_HELD = (TaskStatus.ASSIGNED, TaskStatus.RUNNING, TaskStatus.BLOCKED)  # What a Warden may hold.
_CHECK_S = 1.0  # Plenty against a `lost_after_s` of minutes, and three reads a second at most.
# What the release chain is told for a lost Warden's Cell: never a success, so it is torn down.
_LOST_OUTCOME = TaskOutcome(status=TaskStatus.FAILED, summary="Its Warden was lost.")

log = get_logger(__name__)

__all__ = ["LOST_REASON", "take_back_lost"]


async def take_back_lost(deps: QueenDeps, wardens: Sequence[WardenLink]) -> RequeenCounts | None:
    """Note which Wardens are away holding tasks, and take back those away too long.

    Args:
        deps: The Queen's collaborators; `requeening.lost` is read and written.
        wardens: Every Warden attached now.

    Returns:
        What was done, when a Warden was lost this tick; None otherwise.
    """
    now, book = deps.clock.now(), deps.requeening
    if book.pending is not None:
        return None  # A Requeening is settling: it waits for, and judges, its own Wardens.
    attached = {link.warden_id for link in wardens}
    if not book.lost and deps.dispatch.placed_on <= attached:
        return None  # Every Warden she ever placed on is here: nothing can be away.
    if book.lost_checked_at is not None and (now - book.lost_checked_at).total_seconds() < _CHECK_S:
        return None  # Read the chamber at most once a second, not on every tick.
    book.lost_checked_at = now
    away = await _away(deps, attached)
    # Forget a Warden that came back or holds nothing now; note a newly away one from now.
    for warden_id in set(book.lost) - away:
        del book.lost[warden_id]
    for warden_id in away:
        book.lost.setdefault(warden_id, now)
    gone = {
        w for w, since in book.lost.items() if (now - since).total_seconds() >= book.lost_after_s
    }
    if not gone:
        return None
    counts = await _take_back(deps, gone)
    for warden_id in gone:
        del book.lost[warden_id]
    log.warning("queen.wardens_lost", wardens=len(gone), **asdict(counts))
    return counts


async def _away(deps: QueenDeps, attached: Collection[WardenId]) -> set[WardenId]:
    """Every Warden she placed work on that holds a task, is not attached, and is not held."""
    return {
        task.warden_id
        for task in await _held(deps)
        if task.warden_id is not None
        and task.warden_id in deps.dispatch.placed_on
        and task.warden_id not in attached
        and not deps.dispatch.held.is_held(task.cell_id)
    }


async def _take_back(deps: QueenDeps, gone: Collection[WardenId]) -> RequeenCounts:
    """Take back the lost Wardens' tasks, revoke their grants and release their Cells."""
    # Local, not module-level: the dispatcher imports `hivemind.queen.deps`, which imports this
    # sub-package, so a module-level import here would be a cycle.
    from hivemind.queen.dispatcher.releases import start_release

    async with deps.dispatch.lock:  # Like every write that moves a task in or out of PENDING.
        tasks = [task for task in await _held(deps) if task.warden_id in gone]
        counts = await take_back(deps, tasks, LOST_REASON)
    revoked = await revoke_held_by(deps, gone, LOST_REASON)
    # Beside the tick, like every release: a Virtual Cell is torn down, any other Cell untouched.
    for cell_id in {task.cell_id for task in tasks if task.cell_id is not None}:
        await start_release(deps, cell_id, _LOST_OUTCOME)
    return counts.plus(RequeenCounts(grants_revoked=revoked))


async def _held(deps: QueenDeps) -> list[Task]:
    """Every task a Warden holds and may still be working: ASSIGNED, RUNNING or BLOCKED."""
    found: list[Task] = []
    for status in _HELD:
        # Latency: one local indexed read of the Brood Chamber per status.
        found.extend(
            await deps.chamber.list(TaskFilter(status=status, limit=MAX_TASK_FILTER_LIMIT))
        )
    return found
