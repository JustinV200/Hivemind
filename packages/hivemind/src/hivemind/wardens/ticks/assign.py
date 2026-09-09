"""Handle TaskAssign and GrantIssued: spawn a sub-bee once both halves of a spawn have arrived.

Roadmap step 3.19's own dispatch map: "TaskAssign (Queen) -> if a GrantIssued for
assignment.grant_id has arrived: SPAWN, else park the assignment until it does (a test covers both
orders); local_pool refuses when max_sub_bees is reached -> park." `handle_assign` is that whole
rule, plus the Hive Stand Warden's own retry: when this Warden currently holds no lease at all
(`WardenState.WATCH` from a refused `start()`), it retries the lease exactly once before giving up
and escalating `AlarmKind.CELL_UNREACHABLE` to the Queen, per this Warden's own `start()` docstring.
`handle_grant` is the mirror image for the other arrival order: record the grant, then spawn
whatever assignment was waiting for exactly this `grant_id`.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. These are `hivemind.wardens.warden.Warden`'s own delegates (not general-purpose
    functions: they read and write its private state directly, the same way `hivemind.workers.
    runtime.attempt.AttemptManager` does for `WorkerRuntime`), called from its tick's own dispatch.
    Calls into `hivemind.wardens.spawn` (WardenCellContext, spawn_sub_bee) and waggle only.

Key invariants:
    - `handle_assign` never spawns twice for the same `task_id`: once a matching grant lets it
      through, the assignment is removed from `warden._pending` in the same call that spawns it.
    - A local-pool refusal parks the assignment exactly like a missing grant does; both are the
      same "not yet, try again later" outcome, never an error.

See Also:
    - .claude/roadmap.md step 3.19's own dispatch map for "TaskAssign ... else park... local_pool
      refuses... -> park".
    - hivemind.wardens.spawn for WardenCellContext and spawn_sub_bee, this module's one worker.
    - hivemind.wardens.ticks.alarms for send_alarm_to_queen, reused here for CELL_UNREACHABLE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.wardens.spawn import WardenCellContext, spawn_sub_bee
from hivemind.wardens.ticks.alarms import send_alarm_to_queen
from waggle.messages.forage import GrantIssued
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["handle_assign", "handle_grant"]


async def handle_assign(warden: Warden, assignment: TaskAssign) -> None:
    """Spawn `assignment`'s sub-bee once its GrantIssued has arrived and the local pool has room.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        assignment: The TaskAssign to spawn or park.
    """
    if warden._lease is None or warden._cell is None or warden._session is None:
        await _retry_lease_or_escalate(warden, assignment)
        return
    grant = warden._grants.get(assignment.grant_id)
    if grant is None:
        warden._pending[assignment.task_id] = assignment
        return
    if not warden._local_pool.acquire():
        warden._pending[assignment.task_id] = assignment
        return
    ctx = WardenCellContext(
        warden_id=warden._warden_id,
        deps=warden._deps,
        ceiling=warden._ceiling,
        cell=warden._cell,
        lease=warden._lease,
        session=warden._session,
    )
    sub_bee = await spawn_sub_bee(ctx, assignment, grant)
    warden._sub_bees[sub_bee.worker_id] = sub_bee
    warden._sub_bee_iters[sub_bee.worker_id] = sub_bee.link.receive()


async def handle_grant(warden: Warden, grant: GrantIssued) -> None:
    """Record `grant` and spawn whichever parked assignment was waiting for exactly this grant.

    Args:
        warden: The owning Warden.
        grant: The GrantIssued to record.
    """
    warden._grants[grant.grant_id] = grant
    warden._local_pool.resize(grant.max_sub_bees)
    waiting = [a for a in warden._pending.values() if a.grant_id == grant.grant_id]
    for assignment in waiting:
        warden._pending.pop(assignment.task_id, None)
        await handle_assign(warden, assignment)


async def _retry_lease_or_escalate(warden: Warden, assignment: TaskAssign) -> None:
    """Retry this Warden's own lease once; escalate CELL_UNREACHABLE if it is refused again."""
    await warden.start()
    if warden._lease is not None:
        await handle_assign(warden, assignment)
        return
    await send_alarm_to_queen(
        warden,
        kind=AlarmKind.CELL_UNREACHABLE,
        detail=f"Cell lease refused twice while assigning task {assignment.task_id}.",
        reason="This Warden's own Cell lease could not be re-established.",
        task_id=assignment.task_id,
    )
