"""Take back the tasks a dead Queen left on Wardens that are gone, and settle their holds.

A task placed on a Warden that died with the Queen's process (the Hive Stand's own Warden, a
Level 0 device's is the swarm gate's) or on a Virtual Cell that is gone, or whose Warden never
came back within the grace, can never finish there (ADR-0062). `take_back` gives each one back to
PENDING as a new attempt, the way a lost device's tasks are taken back (`hivemind.queen.
swarm_gate.takeback`): a question it waited on is withdrawn and the human's devices told, its
newest Handoff is noted so its next placement resumes from it rather than starting over, and one
on a Night Veil Cell is cancelled instead, that tier being teardown-only (codingrules 8.7).
`clustering_paused` picks out, from PAUSED tasks, those whose hold was Clustering: the hold that
lived only in the dead process's memory, while a quarantine and an isolation are read off the
trail and stay as they are. `revoke_held_by` returns every grant a gone Warden held.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    requeening sub-package. Called by `.recover`, under the dispatch lock. Calls into
    `hivemind.brood_chamber`, `hivemind.cell` (the tier), `hivemind.forage`,
    `hivemind.pheromone` (TrailQuery), `hivemind.queen.cluster` (latest_handoff),
    `hivemind.queen.forage` (activate, revoke), `hivemind.queen.isolation` (read_isolation),
    `hivemind.queen.swarm_gate` (recall_handoff, imported where used), `.book` and waggle only.

Key invariants:
    - A task another process moved meanwhile, or one that ended, is skipped, never forced.
    - No task leaves here ASSIGNED, RUNNING, BLOCKED or (for a Clustering hold) PAUSED: each is
      PENDING with its attempt advanced, or CANCELLED on a Night Veil Cell.
    - A task held by a quarantine or an isolation is never taken back here: its hold is someone
      else's to lift, and a fresh placement would slip it.

See Also:
    - docs/adr/0062-requeening-recovery-sources-of-truth.md for which tasks are orphans.
    - hivemind.queen.swarm_gate.takeback for the same act for a lost device.
    - hivemind.queen.dispatcher.ready.unfinished for the stop's own give-back.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import TYPE_CHECKING

from hivemind.brood_chamber import InvalidTransitionError, Task, TaskNotFoundError, TaskStatus
from hivemind.brood_chamber.questions import QuestionStatus
from hivemind.cell import CombShieldLevel
from hivemind.common.logging import get_logger
from hivemind.forage import GrantState
from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery
from hivemind.queen.cluster import latest_handoff
from hivemind.queen.forage import activate, revoke
from hivemind.queen.isolation import IsolationState, read_isolation
from hivemind.queen.requeening.book import RequeenCounts
from waggle.ids import MessageId, TaskId, WardenId
from waggle.messages import HandoffRef
from waggle.messages.forage import RevocationCause

if TYPE_CHECKING:
    # Types only: `hivemind.queen.deps` imports this sub-package (`QueenDeps.requeening`).
    from hivemind.queen.deps import QueenDeps

# Why a task is taken back, on its `task.unassigned` and its question's withdrawal alike.
REQUEENED_REASON = (
    "Its Warden died with the Queen that placed it; the restarted Queen places it afresh."
)
# Why one on a Night Veil Cell is cancelled instead.
VEILED_REASON = "Its Warden died with the Queen on a Night Veil Cell, which nothing outlives."
# Why a gone Warden's grants go back to the pool.
REVOKED_REASON = "Its holder did not come back to the restarted Queen."
INTERVENED_KIND = "warden.intervened"  # A quarantine: payload names the task it holds.
RESUMED_KIND = "task.resumed"  # The chamber's resume: subject is the task.

log = get_logger(__name__)

__all__ = [
    "REQUEENED_REASON",
    "REVOKED_REASON",
    "VEILED_REASON",
    "clustering_paused",
    "newest_handoff",
    "revoke_held_by",
    "take_back",
]


async def take_back(deps: QueenDeps, tasks: Sequence[Task]) -> RequeenCounts:
    """Give every task in `tasks` back to PENDING, or cancel it on a Night Veil Cell.

    Args:
        deps: The Queen's collaborators.
        tasks: Tasks placed on Wardens that are gone: ASSIGNED, RUNNING, BLOCKED, or PAUSED by
            Clustering (`clustering_paused`), as last read.

    Returns:
        How many were given back (`given_back`, `paused_replaced` for a paused one) and how many
        cancelled (`night_veil_cancelled`).
    """
    counts = RequeenCounts()
    for task in tasks:
        counts = counts.plus(await _take_one(deps, task))
    return counts


async def clustering_paused(deps: QueenDeps, paused: Sequence[Task]) -> tuple[Task, ...]:
    """Return the PAUSED tasks in `paused` that Clustering held, not a quarantine or isolation.

    Args:
        deps: The Queen's collaborators; the trail is read.
        paused: PAUSED tasks, as last read.

    Returns:
        Those whose Cell is not isolated and that were not quarantined since they last resumed.
    """
    if not paused:
        return ()  # The common case: the trail is never read.
    quarantined = await _quarantined_at(deps)
    held: list[Task] = []
    for task in paused:
        if task.cell_id is not None:
            isolation = await read_isolation(deps, task.cell_id)
            if isolation.state is IsolationState.ISOLATED:
                continue  # The human's lift reopens it; nothing resumes there before.
        if await _quarantined_since_resumed(deps, task.id, quarantined):
            continue  # Its way out is a judge's verdict (`hivemind.queen.quarantine`).
        held.append(task)
    return tuple(held)


async def revoke_held_by(deps: QueenDeps, wardens: Collection[WardenId]) -> int:
    """Revoke every live grant a gone Warden held: nothing draws on it any more.

    Args:
        deps: The Queen's collaborators; `ledger` is written, `trail` recorded to.
        wardens: The Wardens that are gone.

    Returns:
        How many grants were revoked.
    """
    revoked = 0
    for warden_id in wardens:
        for grant in deps.ledger.grants_for(warden_id):
            # ISSUED -> REVOKED is no edge (Appendix C): a grant never drawn on is activated first.
            live = activate(grant) if grant.state is GrantState.ISSUED else grant
            await revoke(deps.ledger, deps, live, RevocationCause.HOLDER_OFFLINE, REVOKED_REASON)
            revoked += 1
    return revoked


async def newest_handoff(deps: QueenDeps, task: Task) -> HandoffRef | None:
    """Return the newest Handoff she can hold for `task`: hers, or its bee's deposit.

    A Warden that is not the Queen's own keeps its bees' Handoffs in a store of its own, so the
    newest she can see may be the HANDOFF Nectar its bee deposited (`recall_handoff`).

    Args:
        deps: The Queen's collaborators.
        task: The task about to resume or be placed afresh.

    Returns:
        The reference to resume from, or None when she holds nothing for it.
    """
    # Local, not module-level: the swarm gate imports `hivemind.queen.deps`, which imports this
    # sub-package, so a module-level import here would be a cycle.
    from hivemind.queen.swarm_gate import recall_handoff

    if task.cell_id is None:
        return await latest_handoff(deps, task.id)
    return await recall_handoff(deps, task.cell_id, task)


async def _take_one(deps: QueenDeps, task: Task) -> RequeenCounts:
    """Take one task back as it now stands; counts of what was done to it (none if it moved)."""
    try:
        task = await deps.chamber.get(task.id)
        if task.status is TaskStatus.BLOCKED and task.pending_question_id is not None:
            task = await _withdraw(deps, task.pending_question_id)
        if task.bound_tier is CombShieldLevel.NIGHT_VEIL:
            # Teardown-only: ended with the Cell its Warden ran, never placed again.
            await deps.chamber.cancel(task.id, VEILED_REASON)
            return RequeenCounts(night_veil_cancelled=1)
        was_paused = task.status is TaskStatus.PAUSED
        # Read before the task leaves its Warden: the newest Handoff of the bee that ran it.
        ref = None if task.status is TaskStatus.ASSIGNED else await newest_handoff(deps, task)
        await deps.chamber.unassign(task.id, REQUEENED_REASON)
    except (InvalidTransitionError, TaskNotFoundError):
        # Moved by another process (an answer from `hive inbox answer`) or gone: left as it is.
        log.info("queen.requeen_skipped", task_id=task.id, status=task.status.value)
        return RequeenCounts()
    if ref is not None:
        deps.dispatch.resumes.note(task.id, ref)  # Its next placement resumes from it.
    return RequeenCounts(paused_replaced=1) if was_paused else RequeenCounts(given_back=1)


async def _withdraw(deps: QueenDeps, question_id: MessageId) -> Task:
    """Withdraw one question and tell the human's devices; return its task, RUNNING again."""
    task = await deps.chamber.withdraw(question_id, REQUEENED_REASON)
    # Its fresh bee asks again once placed; until then no device should offer this one.
    await deps.human_channel.question_closed(question_id, QuestionStatus.WITHDRAWN)
    return task


async def _quarantined_at(deps: QueenDeps) -> dict[TaskId, datetime]:
    """Return each quarantined task's newest `warden.intervened` instant, newest first read."""
    query = TrailQuery(kind=INTERVENED_KIND, newest_first=True, limit=MAX_QUERY_LIMIT)
    found: dict[TaskId, datetime] = {}
    # Latency: one indexed local read of the trail.
    for event in await deps.trail.query(query):
        task_id = event.payload.get("task_id")
        if isinstance(task_id, str) and TaskId(task_id) not in found:
            found[TaskId(task_id)] = event.at
    return found


async def _quarantined_since_resumed(
    deps: QueenDeps, task_id: TaskId, quarantined: dict[TaskId, datetime]
) -> bool:
    """Whether `task_id` was quarantined after it last resumed (or ever, if it never resumed)."""
    at = quarantined.get(task_id)
    if at is None:
        return False
    query = TrailQuery(kind=RESUMED_KIND, subject_id=task_id, newest_first=True, limit=1)
    resumed = await deps.trail.query(query)
    return not resumed or resumed[0].at < at
