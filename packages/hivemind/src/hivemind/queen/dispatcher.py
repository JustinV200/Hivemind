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

`dispatch_ready` fills in three Forage inputs `hivemind.queen.deps.QueenDeps` carries no field for
-- a fixed Drone footprint, a default Royal Reserve, and a fixed grant lifetime -- because the
Queen takes manifest *slices*, and per-role footprints, the reserve and the grant TTL were not part
of the fixed seam this dispatch was given; see this dispatch's own report for the flag.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Called
    unconditionally at the end of every `hivemind.queen.queen.Queen` tick, and once more
    immediately after `submit_goal` and after every `COMPLETE_TASK` decision, so a newly-ready
    task is placed without waiting for the next tick; `redispatch` is called by
    `hivemind.queen.ticks.results.retry_task` and `hivemind.queen.ticks.alarms` for a REBIND.
    Calls into `hivemind.brood_chamber` (Task), `hivemind.forage` (GrantInputs, ModelSlot,
    RoleFootprint, RoyalReserve, grant), `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.queen.placement` (PlacementError, decide), `hivemind.queen.trail` (record_event)
    and waggle only.

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
from hivemind.forage import GrantInputs, ModelSlot, RoleFootprint, RoyalReserve, grant
from hivemind.forage.models.sources import ModelSource
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.placement import Placement, PlacementError, decide
from hivemind.queen.trail import record_event
from waggle.envelope import wrap
from waggle.ids import CellId, GrantId, TaskId, WardenId, new_grant_id
from waggle.messages.task import TaskAssign, WorkerRole

# v0's one role: the Queen always dispatches a Drone. A light, single-seat footprint stands in for
# the manifest's own [forage.roles.drone] table, which QueenDeps carries no field for this phase
# (flagged in this dispatch's report).
_DRONE_FOOTPRINT = RoleFootprint(
    cpu_cores=1.0,
    memory_bytes=512 * 1024 * 1024,
    seats=1,
    token_rate_per_minute=1_000.0,
    exoskeleton_extra_memory_bytes=0,
)
_DEFAULT_RESERVE = RoyalReserve()  # QueenDeps carries no [forage.reserve] slice this phase.
_GRANT_TTL_S = 300.0  # Matches the manifest's own [forage] grant_ttl_s default.

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
    """
    task = await deps.chamber.get(task_id)
    if task.warden_id is None or task.cell_id is None:
        return  # Not placed; a RUNNING task should always be, but there is nothing to resend to.
    link = _link_for(wardens, task.warden_id)
    if link is None:
        return  # Its Warden is no longer attached; nothing to resend to.
    placement = Placement(cell_id=task.cell_id, warden_id=task.warden_id)
    await _send_grant_and_assign(deps, link, task, placement, attempt)


async def _dispatch_one(deps: QueenDeps, wardens: Sequence[WardenLink], task: Task) -> None:
    """Place, grant and assign one ready task, in that order."""
    placement = decide(task.spec.needs, wardens)
    link = _link_for(wardens, placement.warden_id)
    if link is None:
        return  # Defensive: unreachable, since decide() only ever names a Warden from `wardens`.
    await _send_grant_and_assign(deps, link, task, placement, task.attempt)

    reason = "Placed by the Queen's dispatcher."
    await deps.chamber.assign(task.id, placement.warden_id, placement.cell_id, reason)
    await deps.chamber.start(task.id)
    await record_event(
        deps, "queen.assigned", task.id, cell_id=placement.cell_id, warden_id=placement.warden_id
    )


async def _send_grant_and_assign(
    deps: QueenDeps, link: WardenLink, task: Task, placement: Placement, attempt: int
) -> None:
    """Mint a fresh grant and send it, then a TaskAssign at `attempt`, in that order."""
    cell_id, warden_id = placement.cell_id, placement.warden_id
    fresh_grant = grant(_grant_inputs(deps, link, warden_id, cell_id, task))
    sources: dict[str, ModelSource] = {
        binding.source_id: deps.map.get(binding.source_id) for binding in fresh_grant.allowed
    }
    assign = _task_assign(task, cell_id, fresh_grant.id, attempt)
    await link.transport.send(wrap(fresh_grant.to_wire(sources), link.hop, clock=deps.clock))
    await link.transport.send(wrap(assign, link.hop, clock=deps.clock))


def _grant_inputs(
    deps: QueenDeps, link: WardenLink, holder: WardenId, cell_id: CellId, task: Task
) -> GrantInputs:
    """Build one ready task's GrantInputs from its Cell, its Tempo and the Queen's own budgets."""
    return GrantInputs(
        cell_capacity=link.cell.capacity,
        role=WorkerRole.DRONE,
        footprint=_DRONE_FOOTPRINT,
        tempo=task.spec.needs.tempo,
        map=deps.map,
        reserve=_DEFAULT_RESERVE,
        budgets=deps.budgets,
        holder=holder,
        cell_id=cell_id,
        task_id=task.id,
        grant_id=new_grant_id(deps.clock),
        now=deps.clock.now(),
        ttl_s=_GRANT_TTL_S,
    )


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
