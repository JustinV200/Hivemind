"""Resume the tasks of a Warden that clustered fully while its link was down, once it is back.

A Warden whose link to the Queen stays down past `[supervision] max_offline_s` clusters fully
(`hivemind.wardens.offline.freeze.cluster_fully`): every bee stops at a Handoff, and the tasks wait
for a fresh `TaskAssign` from her, since past that limit she has judged the grant lost. A device's
come back through the swarm gate's hold, which takes them back. A Virtual Cell has no such hold
(a Night Veil Cell's Warden redials Tor and is simply attached again), so until now nobody sent
that `TaskAssign`, and its tasks sat RUNNING in the Brood Chamber with no bee behind them.

`resume_after_cut` is that resume. Attaching a Warden she has had before marks it returned
(`DispatchBook.returned`); its first Heartbeat that is past OFFLINE is its own word for how the
cut ended. CLUSTERED means it holds paused tasks it will not start by itself: each task the
chamber has RUNNING on it with no sub-bee row in that Heartbeat is paused and resumed through
`hivemind.queen.dispatcher.resume_paused` from the newest Handoff the Queen can hold for it
(`hivemind.queen.swarm_gate.recall.recall_handoff`, under the refusals every recall applies).
Anything else ends the check: it came back before the limit and carries on by itself.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's cluster
    sub-package. Called by `hivemind.queen.ticks.liveness.handle_heartbeat_item` for every
    Heartbeat. Calls into `hivemind.brood_chamber`, `hivemind.queen.dispatcher` (resume_paused),
    `hivemind.queen.swarm_gate.recall` (recall_handoff), `hivemind.queen.trail` and waggle only;
    the dispatcher and the recall imported where they are used, for the cycle `protocol.py`
    describes.

Key invariants:
    - A Warden is checked once per return, on its first Heartbeat past OFFLINE, so a Heartbeat
      sent before it read the resume's own `TaskAssign` can never resume a task twice.
    - Only a task RUNNING in the chamber, placed on that Warden, with no sub-bee in the Heartbeat
      and on a Cell no hold covers is resumed: one the Queen's own Clustering paused is PAUSED
      already, and a held Cell's tasks are the swarm gate's.
    - Nothing here awaits a model; one `queen.decided` names the tasks, ids only.

See Also:
    - hivemind.wardens.offline.resume for the Warden's side, which waits for this `TaskAssign`.
    - hivemind.queen.cluster.protocol for the Clustering resume this mirrors.
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the offline limit.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.brood_chamber.store import MAX_TASK_FILTER_LIMIT
from hivemind.queen.trail import record_event
from waggle.ids import TaskId, WardenId
from waggle.messages.supervision import Heartbeat, WardenState

if TYPE_CHECKING:
    from hivemind.queen.deps import QueenDeps, WardenLink

RESUME_REASON = "Resumed after its Warden clustered fully while cut off."  # The chamber's event.

__all__ = ["RESUME_REASON", "resume_after_cut"]


async def resume_after_cut(
    deps: QueenDeps, wardens: Sequence[WardenLink], warden_id: WardenId, heartbeat: Heartbeat
) -> tuple[TaskId, ...]:
    """Resume the tasks a returned Warden holds clustered, once its Heartbeat says it does.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        warden_id: The Warden that sent `heartbeat`.
        heartbeat: Its Heartbeat, newest first handled.

    Returns:
        The tasks resumed, oldest first; empty for every Heartbeat but a returned Warden's first
        CLUSTERED one.
    """
    returned = deps.dispatch.returned
    # Still OFFLINE: it has not taken in that the link is back, so its word is not settled yet.
    if warden_id not in returned or heartbeat.warden_state is WardenState.OFFLINE:
        return ()
    returned.discard(warden_id)
    if heartbeat.warden_state is not WardenState.CLUSTERED:
        return ()  # Back before the limit: it starts what it held by itself.
    working = {child.task_id for child in heartbeat.children}
    tasks = await deps.chamber.list(
        TaskFilter(status=TaskStatus.RUNNING, limit=MAX_TASK_FILTER_LIMIT)
    )
    stranded = tuple(
        task.id
        for task in tasks
        if task.warden_id == warden_id
        and task.id not in working
        and not deps.dispatch.held.is_held(task.cell_id)
    )
    for task_id in stranded:
        await _resume_one(deps, wardens, task_id)
    if stranded:
        await _record(deps, warden_id, stranded)
    return stranded


async def _resume_one(deps: QueenDeps, wardens: Sequence[WardenLink], task_id: TaskId) -> None:
    """Pause `task_id` in the chamber, then resume it from the newest Handoff she can hold."""
    # Local, not module-level: both reach back into the queen package (protocol.py's docstring).
    from hivemind.queen.dispatcher import resume_paused
    from hivemind.queen.swarm_gate.recall import recall_handoff

    # RUNNING -> PAUSED -> RUNNING: PAUSED is the one state a resume starts from, and the bee
    # that was RUNNING is gone, stopped at its Handoff by its own Warden.
    await deps.chamber.pause(task_id, RESUME_REASON)
    task = await deps.chamber.get(task_id)
    ref = None if task.cell_id is None else await recall_handoff(deps, task.cell_id, task)
    await resume_paused(deps, wardens, task_id, ref, RESUME_REASON)


async def _record(deps: QueenDeps, warden_id: WardenId, task_ids: tuple[TaskId, ...]) -> None:
    """Record the one `queen.decided` naming the tasks resumed, ids only."""
    await record_event(
        deps,
        "queen.decided",
        warden_id,
        reason="resumed_after_cut",
        task_ids=list(task_ids),
    )
