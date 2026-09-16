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

from hivemind.pheromone import WardenEvent
from hivemind.wardens.spawn import WardenCellContext, spawn_sub_bee
from hivemind.wardens.state import SETTLED_EVENT_KINDS, assert_transition, settled_state
from hivemind.wardens.ticks.alarms import send_alarm_to_queen
from waggle.ids import new_event_id
from waggle.messages.forage import GrantIssued
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

__all__ = ["handle_assign", "handle_grant", "spawn_parked"]


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
    if not warden._sub_bee_slots.acquire():
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
    """Record `grant`, alarm if it now sits below usage, and spawn whatever it unparks.

    Args:
        warden: The owning Warden.
        grant: The GrantIssued to record.
    """
    warden._grants[grant.grant_id] = grant
    # Read before resize(): SubBeeSlots.resize's own docstring leaves in_use as-is even when the
    # new capacity is smaller, so this is the one place that still knows what it used to allow
    # (roadmap step 4.7: "a Warden over its grant gets an Alarm, not a crash").
    in_use_before_resize = warden._sub_bee_slots.in_use
    warden._sub_bee_slots.resize(grant.max_sub_bees)
    if grant.max_sub_bees == 0:
        # Nothing can ever spawn under this grant, so every assignment it covers would park in
        # `_pending` with no trace on the trail (the first local run sat that way for minutes).
        # Only the Queen divides Forage (GRANT_EXCEEDED's own policy row: escalate, never retry),
        # so say so up the chain rather than wait for a larger grant that may never come.
        await send_alarm_to_queen(
            warden,
            kind=AlarmKind.GRANT_EXCEEDED,
            detail=f"Grant {grant.grant_id} allows zero sub-bees; no assignment under it can "
            "start (check the Forage map's seats against [forage.reserve]).",
            reason="A zero-bee grant needs a larger grant from the Queen; a Warden cannot "
            "enlarge its own.",
            task_id=grant.task_id,
        )
    elif in_use_before_resize > grant.max_sub_bees:
        # A shrink (queen.forage.grants) landed below what is already running: the pool refuses
        # every new acquire() until enough sub-bees finish on their own, but nothing said so on
        # the trail until now.
        await send_alarm_to_queen(
            warden,
            kind=AlarmKind.GRANT_EXCEEDED,
            detail=f"Grant {grant.grant_id} shrank max_sub_bees to {grant.max_sub_bees}, below "
            f"the {in_use_before_resize} sub-bees already running under it.",
            reason="A shrunk grant put this Warden over its own ceiling; only the Queen can "
            "grow it back, and no new sub-bee can start meanwhile.",
            task_id=grant.task_id,
        )
    waiting = [a for a in warden._pending.values() if a.grant_id == grant.grant_id]
    for assignment in waiting:
        warden._pending.pop(assignment.task_id, None)
        await handle_assign(warden, assignment)


async def spawn_parked(warden: Warden) -> None:
    """Spawn every parked assignment whose grant is known once the local pool has room again.

    `handle_grant` re-drives an assignment parked for a missing grant; this re-drives one parked
    for a full pool, which nothing else did: a bee finishing (`retire_sub_bee`'s own `release`)
    freed a slot that no later event ever handed to the assignment still waiting for it. Called
    once per Warden tick, so the cost is one scan of a normally-empty dict.

    Args:
        warden: The owning Warden.
    """
    pool = warden._sub_bee_slots
    for assignment in tuple(warden._pending.values()):
        if pool.in_use >= pool.capacity:
            return
        if assignment.grant_id in warden._grants:
            warden._pending.pop(assignment.task_id, None)
            await handle_assign(warden, assignment)


async def settle_after_tick(warden: Warden) -> None:
    """Hand a slot a finished bee freed to a parked assignment, then settle ACTIVE/WATCH/CLUSTERED.

    `spawn_parked` runs first so a task parked for a full pool starts the same tick the pool has
    room again (nothing else ever re-drove it); `hivemind.wardens.state.settled_state` then reads
    the sub-bee table (and, roadmap step 4.9, `warden._clustered_tasks`) that spawn may just have
    grown, through `assert_transition`, so a state this pair can never legally reach (CLUSTERED
    with no sub-bees left, say) raises loudly rather than being written silently. Every settled
    state, CLUSTERED included, records its own `warden.*` event (`SETTLED_EVENT_KINDS`), beside
    the Queen's own `queen.clustered`. Lives here rather than in warden.py so the kernel file stays
    inside its size cap (codingrules 5.1); it is the second half of this module's spawn duty.
    """
    await spawn_parked(warden)
    target = settled_state((s.task_id for s in warden._sub_bees.values()), warden._clustered_tasks)
    if target is not warden._state:  # Something changed since the last check.
        assert_transition(warden._state, target, warden_id=warden._warden_id)
        warden._state = target
        # Same shape as warden.py's own _record_event: subject is this Warden, no payload.
        event = WardenEvent(
            id=new_event_id(warden._deps.clock),
            hive_id=warden._deps.identity.hive_id,
            node_id=warden._deps.identity.node_id,
            at=warden._deps.clock.now(),
            actor=warden._deps.identity.actor,
            kind=SETTLED_EVENT_KINDS[target],
            subject_id=warden._warden_id,
            payload={},
        )
        await warden._deps.trail.record(event)


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
