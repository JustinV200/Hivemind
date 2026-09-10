"""Define dispatch_ready and redispatch: place, grant and (re-)assign a task the Queen wants run.

Roadmap step 3.20's own dispatch map: for each `chamber.next_ready` task, `hivemind.queen.
placement.decide` picks a Cell and a Warden, `hivemind.forage.allocate.grant` computes a fresh
`ForageGrant`, and the Warden receives a `GrantIssued` followed by a `TaskAssign` -- in that order,
so a Warden never sees an assignment its grant has not already arrived for
(`hivemind.wardens.ticks.assign.handle_assign`'s own park-until-both-arrive contract). Once both
are sent, `chamber.assign` then `chamber.start` move the task PENDING -> ASSIGNED -> RUNNING and
`queen.assigned` records it. The Queen never assigns a Worker directly (codingrules section 8.8):
every assignment goes to a Warden, which is what actually spawns.

`redispatch` is the sibling `hivemind.queen.ticks.results.retry_task` and an alarm-driven REBIND
call for: a RUNNING task's own `hivemind.brood_chamber.task.state.TRANSITIONS` has no edge back to
PENDING or ASSIGNED (only `ASSIGNED -> PENDING`, for a Warden lost before its Worker ever started),
so a retry can never go through `chamber.unassign` the way a fresh dispatch goes through
`chamber.assign`. It resends a fresh grant and assignment straight to the Cell and Warden the task
is already placed on, at the caller's own attempt number, leaving the chamber's own `Task.status`
at RUNNING throughout -- the same way a Warden's own internal RETRY/REBIND never tells the Brood
Chamber anything happened at all.

`dispatch_ready` reads its three Forage inputs -- the Drone's footprint, the Royal Reserve and the
grant lifetime -- from `deps.footprints`/`deps.reserve`/`deps.grant_ttl_s` (roadmap step 3.21,
second half added these three `QueenDeps` fields, defaulted to the module constants this file used
to carry itself, so `hivemind.cli.compose.build_hive` can bind them to the loaded manifest's own
`[forage.roles.drone]`/`[forage.reserve]`/`[forage] grant_ttl_s` while every existing test, which
never names these fields, keeps today's behaviour unchanged).

This module also records `forage.granted` right after sending a fresh grant: nothing else in the
Hive's committed code recorded that `hivemind.pheromone.events.families.ForageEvent` kind despite
it already existing in `ForageEvent.KINDS` -- neither the Queen's own dispatch nor the Warden's
`hivemind.wardens.ticks.assign.handle_grant` wrote one. Roadmap step 3.21 (second half)'s own
required trail order (`granted` between `placed` and `spawned`) and `hive wardens list` (reading
"the grants issued to it") both need it to exist, so this dispatch adds the one call, here, where
the grant is already in hand; `hivemind.wardens.**` is outside this dispatch's owned files, so the
event is built directly rather than through `hivemind.queen.trail.record_event` (which only ever
builds a `queen.*` `QueenEvent`) -- flagged in this dispatch's own report as a pre-existing gap,
not a new rule this module invents.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    unconditionally at the end of every `hivemind.queen.queen.Queen` tick, and once more
    immediately after `submit_goal` and after every `COMPLETE_TASK` decision, so a newly-ready
    task is placed without waiting for the next tick; `redispatch` is called by
    `hivemind.queen.ticks.results.retry_task` and `hivemind.queen.ticks.alarms` for a REBIND.
    Calls into `hivemind.brood_chamber` (Task), `hivemind.forage` (ForageGrant, GrantInputs,
    ModelSlot, grant), `hivemind.pheromone` (ForageEvent), `hivemind.queen.deps` (QueenDeps,
    WardenLink), `hivemind.queen.placement` (PlacementError, decide), `hivemind.queen.trail`
    (record_event) and waggle only.

Key invariants:
    - `GrantIssued` is always sent before `TaskAssign`, on the same Warden link, for the same task,
      whether from a fresh dispatch or a retry.
    - A `PlacementError` for one ready task never stops the others: `dispatch_ready` records the
      failure on the trail and moves on to the next ready task, bounded to at most one attempt per
      task id per call so it can never loop forever on a task that will never fit.
    - `chamber.assign`/`chamber.start` run only for a fresh dispatch, never for `redispatch`: a
      RUNNING task's own status is untouched by a retry.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the exact TaskAssign shape this module
      builds.
    - .claude/codingrules.md section 8.8 for "never assigns to a Worker directly".
    - .claude/codingrules.md Appendix C, "Task" row, for the TRANSITIONS table `redispatch`'s own
      docstring explains working around.
    - hivemind.queen.placement for decide, this module's one placement call.
    - hivemind.forage.allocate for grant, this module's one allocation call.
"""

from __future__ import annotations

from collections.abc import Sequence

from hivemind.brood_chamber import Task
from hivemind.forage import ForageGrant, GrantInputs, ModelSlot, grant
from hivemind.forage.models.sources import ModelSource
from hivemind.pheromone import ForageEvent
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.placement import Placement, PlacementError, decide
from hivemind.queen.trail import record_event
from waggle.envelope import wrap
from waggle.ids import CellId, GrantId, TaskId, WardenId, new_event_id, new_grant_id
from waggle.messages.task import TaskAssign, WorkerRole

__all__ = ["dispatch_ready", "redispatch"]


async def dispatch_ready(deps: QueenDeps, wardens: Sequence[WardenLink]) -> None:
    """Place, grant and assign every task `deps.chamber.next_ready` currently offers.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; placement picks among these.

    Returns:
        None, once every ready task has been dispatched or found unplaceable this call.
    """
    attempted: set[TaskId] = set()
    while True:
        task = await deps.chamber.next_ready()
        if task is None or task.id in attempted:
            return  # Nothing left ready, or we would only re-attempt a task already tried.
        attempted.add(task.id)
        try:
            await _dispatch_one(deps, wardens, task)
        except PlacementError:
            # The reason string on the trail is enough; the next ready task still gets a chance.
            await record_event(deps, "queen.decided", task.id, reason="placement_failed")


async def redispatch(
    deps: QueenDeps, wardens: Sequence[WardenLink], task_id: TaskId, attempt: int
) -> None:
    """Resend a fresh grant and assignment to a RUNNING task's own Cell and Warden (a retry).

    Never touches the task's own chamber status (module docstring): RUNNING has no edge back to
    PENDING or ASSIGNED, so a retry is a wire-level re-send to wherever the task is already
    placed, not a fresh placement decision.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        task_id: The task to retry; must already be placed (have a `warden_id` and `cell_id`).
        attempt: The attempt number to stamp on the fresh `TaskAssign`.

    Returns:
        None, once a fresh grant and assignment have been sent, or silently if the task is
        somehow not placed or its Warden is no longer attached (nothing to resend to).

    `_dispatch_one`'s own fix 4a (chamber transitions before either wire send) has nothing to
    reorder here: `redispatch` never calls `chamber.assign`/`chamber.start` at all (module
    docstring), so there is no PENDING/ASSIGNED window for a fast Warden's own reaction to land
    inside in the first place.
    """
    task = await deps.chamber.get(task_id)
    if task.warden_id is None or task.cell_id is None:
        return  # Not placed; a RUNNING task should always be, but there is nothing to resend to.
    link = _link_for(wardens, task.warden_id)
    if link is None:
        return  # Its Warden is no longer attached; nothing to resend to.
    placement = Placement(cell_id=task.cell_id, warden_id=task.warden_id)
    fresh_grant = await _send_grant_and_assign(deps, link, task, placement, attempt)
    await _record_forage_granted(deps, task, fresh_grant, task.warden_id)


async def _dispatch_one(deps: QueenDeps, wardens: Sequence[WardenLink], task: Task) -> None:
    """Place, grant and assign one ready task, in that order.

    The chamber's own PENDING -> ASSIGNED -> RUNNING transition, and `queen.assigned`, land
    BEFORE either wire message is sent (this dispatch's own fix 4a): a real Warden reacting to
    `TaskAssign` can, on genuine SQLite I/O, run faster than the Queen's own remaining
    bookkeeping -- and a Drone's own immediate Question forwarded straight back up
    (`Queen._act`'s `BLOCK_ON_QUESTION` handling) must never find the chamber still reading
    ASSIGNED while it tries to move a RUNNING task to BLOCKED.
    """
    placement = decide(task.spec.needs, wardens)
    link = _link_for(wardens, placement.warden_id)
    if link is None:
        return  # Defensive: unreachable, since decide() only ever names a Warden from `wardens`.
    reason = "Placed by the Queen's dispatcher."
    await deps.chamber.assign(task.id, placement.warden_id, placement.cell_id, reason)
    await deps.chamber.start(task.id)
    await record_event(
        deps, "queen.assigned", task.id, cell_id=placement.cell_id, warden_id=placement.warden_id
    )
    fresh_grant = await _send_grant_and_assign(deps, link, task, placement, task.attempt)
    # "placed" (queen.assigned, just above) precedes "granted" on the trail (module docstring's
    # own required order): this Queen-side record only exists because nothing else writes
    # forage.granted at all.
    await _record_forage_granted(deps, task, fresh_grant, placement.warden_id)


async def _send_grant_and_assign(
    deps: QueenDeps, link: WardenLink, task: Task, placement: Placement, attempt: int
) -> ForageGrant:
    """Mint a fresh grant and send it, then a TaskAssign at `attempt`, in that order."""
    cell_id, warden_id = placement.cell_id, placement.warden_id
    fresh_grant = grant(_grant_inputs(deps, link, warden_id, cell_id, task))
    sources: dict[str, ModelSource] = {
        binding.source_id: deps.map.get(binding.source_id) for binding in fresh_grant.allowed
    }
    assign = _task_assign(task, cell_id, fresh_grant.id, attempt)
    await link.transport.send(wrap(fresh_grant.to_wire(sources), link.hop, clock=deps.clock))
    await link.transport.send(wrap(assign, link.hop, clock=deps.clock))
    return fresh_grant


def _grant_inputs(
    deps: QueenDeps, link: WardenLink, holder: WardenId, cell_id: CellId, task: Task
) -> GrantInputs:
    """Build one ready task's GrantInputs from its Cell, its Tempo and the Queen's own budgets."""
    return GrantInputs(
        cell_capacity=link.cell.capacity,
        role=WorkerRole.DRONE,
        footprint=deps.footprints[WorkerRole.DRONE],
        tempo=task.spec.needs.tempo,
        map=deps.map,
        reserve=deps.reserve,
        budgets=deps.budgets,
        holder=holder,
        cell_id=cell_id,
        task_id=task.id,
        grant_id=new_grant_id(deps.clock),
        now=deps.clock.now(),
        ttl_s=deps.grant_ttl_s,
    )


async def _record_forage_granted(
    deps: QueenDeps, task: Task, fresh_grant: ForageGrant, warden_id: WardenId
) -> None:
    """Record the one `forage.granted` ForageEvent no other module writes (module docstring)."""
    event = ForageEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="forage.granted",
        subject_id=fresh_grant.id,
        payload={"task_id": task.id, "warden_id": warden_id},
    )
    await deps.trail.record(event)


def _task_assign(task: Task, cell_id: CellId, grant_id: GrantId, attempt: int) -> TaskAssign:
    """Build the TaskAssign a task's Warden receives, at `attempt`."""
    return TaskAssign(
        task_id=task.id,
        goal_id=task.goal_id,
        cell_id=cell_id,
        role=WorkerRole.DRONE,
        slot=ModelSlot.WORKER.to_wire(),
        objective=task.spec.objective,
        acceptance=task.spec.acceptance,
        tempo=task.spec.needs.tempo.to_wire(),
        clearance=task.spec.clearance.to_wire(),
        grant_id=grant_id,
        attempt=attempt,
        resume_from=None,
        reason="Placed on the Hive Stand by the Queen's dispatcher.",
    )


def _link_for(wardens: Sequence[WardenLink], warden_id: WardenId) -> WardenLink | None:
    """Return the WardenLink named by `warden_id`, or None when it names no attached Warden."""
    return next((link for link in wardens if link.warden_id == warden_id), None)
