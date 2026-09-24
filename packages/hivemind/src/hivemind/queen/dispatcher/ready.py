"""Define dispatch_ready and redispatch: place, grant and (re-)assign a task the Queen wants run.

Roadmap step 3.20's own dispatch map, now over `hivemind.queen.placement.decide`'s own three-way
`Placement` (roadmap step 5.7, ADR-0028): for each `chamber.next_ready` task, `hivemind.queen.
dispatcher.snapshot` builds the pure `Inventory`/`ForageView` snapshot `decide` reads, `decide`
picks a `Placement`, `hivemind.queen.dispatcher.acquire.resolve_link` turns it into a `WardenLink`
(acquiring a Virtual Cell through the `VirtualCellProvider` seam first, if it names one),
`hivemind.forage.allocate.grant` computes a fresh `ForageGrant`, and the Warden receives a
`GrantIssued` followed by a `TaskAssign` -- in that order, so a Warden never sees an assignment its
grant has not already arrived for (`hivemind.wardens.ticks.assign.handle_assign`'s own
park-until-both-arrive contract). Once both are sent, `chamber.assign` then `chamber.start` move
the task PENDING -> ASSIGNED -> RUNNING, `queen.placed` records the placement's own reason (the
wax that weighed on it included) and `queen.assigned` records the Cell it landed on. The Queen
never assigns a Worker directly (codingrules section 8.8): every assignment goes to a Warden,
which is what actually spawns.

`redispatch` is the sibling `hivemind.queen.ticks.results.retry_task` and an alarm-driven REBIND
call for: a RUNNING task's own `hivemind.brood_chamber.task.state.TRANSITIONS` has no edge back to
PENDING or ASSIGNED (only `ASSIGNED -> PENDING`, for a Warden lost before its Worker ever started),
so a retry can never go through `chamber.unassign` the way a fresh dispatch goes through
`chamber.assign`. It resends a fresh grant and assignment straight to the Cell and Warden the task
is already placed on (no placement decision at all), leaving the chamber's own `Task.status` at
RUNNING throughout -- the same way a Warden's own internal RETRY/REBIND never tells the Brood
Chamber anything happened at all.

`_send_grant_and_assign` is also the one choke point that used to send a grant computing to
`max_sub_bees == 0` (a Cell so out of headroom that not even one Drone fits) exactly like every
other grant: the Warden then raised `GRANT_EXCEEDED` ("allows zero sub-bees"), the Queen escalated
that to the human inbox, and the task sat RUNNING until its whole timeout elapsed with no Drone and
nothing in `hive run`'s own view saying why (`.claude/phase-4-handoff.md` section 4.2 item 1, found
for real on 2026-09-20 when low free RAM sized a grant to zero). Since this fix, a fresh grant that
computes to `max_sub_bees < 1` is never sent: it is recorded as `forage.denied` instead, carrying
the allocator's own `reason` string plus the free-memory, reserve and seat figures that produced
zero, and the task fails at once (`task.failed`) with that reason as its outcome summary, so an
operator watching `hive run` sees the cause on screen in seconds. Capacity is probed once, at lease
time (codingrules section 8.10), so nothing here waits for memory to free up; failing fast is the
right v0 behaviour. `redispatch` and `resume_paused` (a RUNNING retry and a resumed PAUSED task,
respectively) hit this same choke point and fail the same way, since both funnel through
`_send_grant_and_assign` too. A placement no Cell can bear at all never reaches this point:
`hivemind.queen.placement`'s own fit rule refuses the Cell first (roadmap step 5.7), so what is
left here is the narrower case of a Cell that fits on paper but has no headroom left by the time
the grant is sized.

Roadmap step 10.3 (ADR-0031) wires two enforcement points through here. `placement`: a
`PlacementError` now records its own message on `queen.decided` (it used to record only
"placement_failed"), and when the task's goal set alone left no candidate (`PlacementError.
denied`) the Queen, acting for the goal, checks each missing capability through her `Enforcer`,
which records the `guard.denied`. `grant_issue`: every fresh grant passes `hivemind.queen.
dispatcher.grants.authorize_grant` before it is sent, and one left with no model binding is
refused exactly like a zero-bee grant (`forage.denied`, the task failed). The `TaskAssign` carries
the goal's capability set and the task's network scopes (Waggle 1.6), so the Warden can attenuate
its Worker's set to both.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher`
    sub-package. Called unconditionally at the end of every `hivemind.queen.queen.Queen` tick, and
    once more immediately after `submit_goal` and after every `COMPLETE_TASK` decision, so a
    newly-ready task is placed without waiting for the next tick; `redispatch` is called by
    `hivemind.queen.ticks.results.retry_task` and `hivemind.queen.ticks.alarms` for a REBIND.
    Calls into `hivemind.brood_chamber` (Task), `hivemind.cell` (Cell), `hivemind.forage`
    (Ceilings, ForageGrant, GrantInputs, ModelSlot, grant), `hivemind.pheromone` (ForageEvent),
    `hivemind.guard` (the placement point), `hivemind.pheromone` (MAX_PAYLOAD_STRING_CHARS),
    `hivemind.queen.authority`, `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.queen.forage.grants` (activate, roadmap step 4.7), `hivemind.queen.placement`
    (Placement, PlacementError, ProvisionVirtual, ReuseDormant, ReuseReal, decide),
    `hivemind.queen.dispatcher.acquire`/`.grants`/`.snapshot`, `hivemind.queen.trail`
    (record_event, record_forage_event) and waggle only.

Key invariants:
    - `GrantIssued` is always sent before `TaskAssign`, on the same Warden link, for the same task,
      whether from a fresh dispatch or a retry.
    - A `PlacementError` for one ready task never stops the others: `dispatch_ready` records the
      failure on the trail and moves on to the next ready task, bounded to at most one attempt per
      task id per call so it can never loop forever on a task that will never fit.
    - `chamber.assign`/`chamber.start` run only for a fresh dispatch, never for `redispatch`: a
      RUNNING task's own status is untouched by a retry.
    - `queen.placed` is always recorded before `queen.assigned`, for a fresh dispatch: the reason a
      Cell was chosen precedes the record that it was actually assigned.
    - A fresh grant with `max_sub_bees < 1`, or with no model binding left once
      `authorize_grant` has run, is never sent to a Warden: `_send_grant_and_assign` records
      `forage.denied` and fails the task (RUNNING -> FAILED) instead, whether the grant came from
      a fresh dispatch, a retry or a resume.
    - `guard.denied` for placement is recorded only when the goal's capability set alone left no
      candidate; a capacity or fit failure records `queen.decided` and nothing more.

See Also:
    - .claude/roadmap.md step 5.7 for "records queen.placed with the reason, the wax that weighed
      on it included".
    - .claude/codingrules.md section 8.8 for "never assigns to a Worker directly".
    - hivemind.queen.placement for decide, this module's one placement call.
    - hivemind.queen.dispatcher.acquire for resolve_link, this module's Placement-to-Warden call.
    - hivemind.forage.allocate for grant, this module's one allocation call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import JsonValue

from hivemind.brood_chamber import Task, TaskOutcome, TaskStatus
from hivemind.cell import Cell
from hivemind.forage import Ceilings, ForageGrant, GrantInputs, ModelSlot, grant
from hivemind.forage.models.sources import ModelSource
from hivemind.guard import CapabilitySet, EnforcementPoint
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS, ForageEvent
from hivemind.queen.authority import goal_held, request_for, task_context
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.acquire import resolve_link
from hivemind.queen.dispatcher.grants import authorize_grant
from hivemind.queen.dispatcher.snapshot import build_forage_view, build_inventory
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.forage.ceilings import set_ceilings
from hivemind.queen.forage.hosting import write_hosting_plan
from hivemind.queen.placement import Placement, PlacementError, ProvisionVirtual, decide
from hivemind.queen.trail import record_event, record_forage_event
from waggle.envelope import wrap
from waggle.ids import CellId, GrantId, TaskId, WardenId, new_event_id, new_grant_id
from waggle.messages import HandoffRef
from waggle.messages.task import TaskAssign, WorkerRole

__all__ = ["dispatch_ready", "redispatch", "resume_paused"]


async def dispatch_ready(deps: QueenDeps, wardens: Sequence[WardenLink]) -> None:
    """Place, grant and assign every task `deps.chamber.next_ready` currently offers.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; placement picks among these plus any Virtual
            side `deps.virtual_backends`/`.dormant_cells` names.

    Returns:
        None, once every ready task has been dispatched or found unplaceable this call.
    """
    # One dispatch pass at a time per Queen: Queen.submit_goal and the tick loop both call this,
    # and a Virtual placement awaits a real provision inside resolve_link, a window in which the
    # other caller would otherwise see the same PENDING task and lose the chamber's own
    # PENDING -> ASSIGNED transition (QueenDeps.dispatch_lock's own comment).
    async with deps.dispatch_lock:
        attempted: set[TaskId] = set()
        while True:
            task = await deps.chamber.next_ready()
            if task is None or task.id in attempted:
                return  # Nothing left ready, or we would only re-attempt a task already tried.
            attempted.add(task.id)
            try:
                await _dispatch_one(deps, wardens, task)
            except PlacementError as exc:
                # The next ready task still gets a chance; this one stays PENDING to retry later.
                await _record_placement_failure(deps, task, exc)


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
        None, once a fresh grant and assignment have been sent; silently if the task is somehow
        not placed or its Warden is no longer attached (nothing to resend to); or having instead
        denied the grant and failed the task if it computed to `max_sub_bees < 1` (module
        docstring's own zero-grant fix -- a retry hits the same choke point a fresh dispatch does).
    """
    task = await deps.chamber.get(task_id)
    if task.warden_id is None or task.cell_id is None:
        return  # Not placed; a RUNNING task should always be, but there is nothing to resend to.
    link = _link_for(wardens, task.warden_id)
    if link is None:
        return  # Its Warden is no longer attached; nothing to resend to.
    fresh_grant = await _send_grant_and_assign(deps, link, task, _AssignmentTerms(attempt=attempt))
    if fresh_grant is None:
        return  # Denied: _send_grant_and_assign already failed the task (module docstring).
    await _record_forage_granted(deps, task, fresh_grant, task.warden_id)


async def resume_paused(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    task_id: TaskId,
    resume_from: HandoffRef | None,
    reason: str,
) -> None:
    """Resume a PAUSED task from `resume_from` (or fresh), through the grant-then-assign path.

    Roadmap step 4.9 (Clustering): `hivemind.queen.cluster.protocol.resume`'s one dispatcher-path
    entry point, so a resumed task is re-assigned exactly the way a fresh dispatch is (a fresh
    grant, `GrantIssued` before `TaskAssign`), never a hand-rolled wire send. Unlike `redispatch`
    (a RUNNING task's own retry, which never touches chamber status), this always moves the task
    PAUSED -> RUNNING first, through `hivemind.brood_chamber.chamber.lifecycle._LifecycleMixin.
    resume`, the one legal edge back from PAUSED (Appendix C's "Task" row).

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        task_id: The PAUSED task to resume; must already be placed (have a `warden_id`/`cell_id`
            from before it was paused -- Clustering never unassigns).
        resume_from: The Handoff to resume from, or None to start the fresh attempt without one.
        reason: Why it resumes now, for the chamber's own trail event.

    Returns:
        None, once resumed; silently if the task is somehow not placed or its Warden is no longer
        attached (nothing to resume it through); or having instead denied the grant and failed the
        now-RUNNING task if it computed to `max_sub_bees < 1` (module docstring's own zero-grant
        fix -- a resumed task, `resume_from` included, hits the same choke point a fresh dispatch
        does).
    """
    task = await deps.chamber.get(task_id)
    if task.warden_id is None or task.cell_id is None:
        return  # Not placed; Clustering never unassigns, so this should not happen in practice.
    link = _link_for(wardens, task.warden_id)
    if link is None:
        return  # Its Warden is no longer attached; nothing to resume it through.
    await deps.chamber.resume(task_id, reason)
    # A fresh bee (TaskAssign.resume_from's own field docstring: "a fresh one for a fresh bee");
    # the Queen owns the attempt number on every Queen-to-Warden hop.
    terms = _AssignmentTerms(attempt=task.attempt + 1, resume_from=resume_from)
    fresh_grant = await _send_grant_and_assign(deps, link, task, terms)
    if fresh_grant is None:
        return  # Denied: _send_grant_and_assign already failed the task (module docstring).
    await _record_forage_granted(deps, task, fresh_grant, task.warden_id)


async def _dispatch_one(deps: QueenDeps, wardens: Sequence[WardenLink], task: Task) -> None:
    """Place, grant and assign one ready task, in that order.

    The chamber's own PENDING -> ASSIGNED -> RUNNING transition, and `queen.assigned`, land
    BEFORE either wire message is sent: a real Warden reacting to `TaskAssign` can, on genuine
    SQLite I/O, run faster than the Queen's own remaining bookkeeping -- and a Drone's own
    immediate Question forwarded straight back up (`Queen._act`'s `BLOCK_ON_QUESTION` handling)
    must never find the chamber still reading ASSIGNED while it tries to move a RUNNING task to
    BLOCKED.
    """
    inventory = await build_inventory(deps, wardens)
    placement = decide(
        task.spec.needs, inventory, build_forage_view(deps, task), deps.placement_policy
    )
    link, placement = await resolve_link(deps, wardens, task, placement)
    await _record_placed(deps, task, placement)
    await deps.chamber.assign(
        task.id, link.warden_id, link.cell.id, "Placed by the Queen's dispatcher."
    )
    await deps.chamber.start(task.id)
    await record_event(
        deps, "queen.assigned", task.id, cell_id=link.cell.id, warden_id=link.warden_id
    )
    fresh_grant = await _send_grant_and_assign(
        deps, link, task, _AssignmentTerms(attempt=task.attempt)
    )
    if fresh_grant is None:
        return  # Denied: _send_grant_and_assign already failed the task (module docstring).
    # "placed" and "assigned" (both just above) precede "granted" on the trail.
    await _record_forage_granted(deps, task, fresh_grant, link.warden_id)


async def _record_placement_failure(deps: QueenDeps, task: Task, error: PlacementError) -> None:
    """Record why `task` found no Cell, and refuse each capability its goal alone lacked.

    `queen.decided` carries the error's own message (every rule that eliminated a candidate),
    bounded to the trail's per-string limit. When the goal's set alone left no candidate
    (`error.denied`, roadmap step 10.3), the Queen acting for the goal checks each missing
    capability at the placement point, so each is a `guard.denied` row with its reason.
    """
    detail = str(error)[:MAX_PAYLOAD_STRING_CHARS]
    await record_event(deps, "queen.decided", task.id, reason="placement_failed", detail=detail)
    held = goal_held(task) or CapabilitySet.empty()  # `denied` is only ever set for a goal set.
    context = task_context(task)
    for needed in error.denied:
        request = request_for(deps, EnforcementPoint.PLACEMENT, needed, held)
        await deps.enforcer.check(request.model_copy(update={"context": context}))


async def _record_placed(deps: QueenDeps, task: Task, placement: Placement) -> None:
    """Record `queen.placed`: the placement's own reason, with any Cell or Virtual spec it names.

    Roadmap step 5.7: "Records queen.placed with the reason, the wax that weighed on it included"
    -- `placement.reason` already names any Cell Wax that weighed on the decision
    (`hivemind.queen.placement.decide._real_exclusion_reason` folds a BLOCK note's id and text
    straight into it), so this only adds the ids `reason` itself does not carry as structured data.
    """
    payload: dict[str, JsonValue] = {
        "reason": placement.reason,
        "outcome": type(placement).__name__,
    }
    if isinstance(placement, ProvisionVirtual):
        payload["image"] = placement.spec.image
        payload["backend"] = placement.backend
    else:
        payload["cell_id"] = placement.cell_id
    await record_event(deps, "queen.placed", task.id, **payload)


@dataclass(frozen=True, slots=True)
class _AssignmentTerms:
    """Bundles `attempt`/`resume_from` so `_send_grant_and_assign` stays within codingrules 5.1.

    Attributes:
        attempt: The attempt number to stamp on the fresh TaskAssign.
        resume_from: The Handoff to resume from (roadmap step 4.9's own `resume_paused`); None
            for a fresh dispatch or an ordinary retry (`dispatch_ready`/`redispatch`'s own calls).
    """

    attempt: int
    resume_from: HandoffRef | None = None


async def _send_grant_and_assign(
    deps: QueenDeps, link: WardenLink, task: Task, terms: _AssignmentTerms
) -> ForageGrant | None:
    """Mint a fresh grant, record it live in the ledger, and send it then a TaskAssign.

    Returns None instead, having denied the grant and failed `task`, when the fresh grant computes
    to `max_sub_bees < 1` (module docstring's own zero-grant fix): a grant that empty can run no
    Drone at all, so it is never sent.
    """
    cell_id, warden_id = link.cell.id, link.warden_id
    # Roadmap step 4.8's own wiring step: the first dispatch ever sent to a Warden sets its
    # ceilings and writes its Cell's hosting plan first (hivemind.wardens.ticks.control handles
    # both on arrival); every later dispatch to the same Warden is a no-op here.
    await _ensure_warden_provisioned(deps, link)
    inputs = _grant_inputs(deps, link, warden_id, cell_id, task)
    fresh_grant = grant(inputs)
    # The one choke point (module docstring's own zero-grant fix): sending this anyway is what
    # used to park the task RUNNING until its whole timeout elapsed, with nothing on screen saying
    # why (`.claude/phase-4-handoff.md` section 4.2 item 1).
    if fresh_grant.max_sub_bees < 1:
        await _deny_zero_grant(deps, task, inputs, fresh_grant)
        return None
    # Roadmap step 10.3's grant_issue point: a binding neither set allows never leaves; a grant
    # with none left can run no bee at all, so it is refused the same way a zero-bee one is.
    fresh_grant = await authorize_grant(deps, link, task, fresh_grant)
    if not fresh_grant.allowed:
        await _deny_zero_grant(deps, task, inputs, fresh_grant)
        return None
    # roadmap step 4.7: the ledger is the live book of every shared grant, not only the ones a
    # ForageRequest later grows; activate() moves it past ISSUED since a task dispatch means the
    # Warden is about to draw on it at once. The Cell's own capacity is reported here too, from the
    # same Cell object placement already resolved, so the ledger's headroom has real figures.
    await deps.ledger.report_capacity(cell_id, link.cell.capacity)
    await deps.ledger.record_grant(forage_grants.activate(fresh_grant))
    # A Virtual Cell's own READY -> GRANTED edge (hivemind.hive.cell_state) is driven here, by the
    # dispatch that grants it, so `cell.granted` precedes the assignment on the trail; the seam is
    # a no-op for a Cell the lifecycle does not track (QueenDeps.on_cell_granted's own comment).
    if deps.on_cell_granted is not None:
        await deps.on_cell_granted(cell_id, fresh_grant.id)
    sources: dict[str, ModelSource] = {
        binding.source_id: deps.map.get(binding.source_id) for binding in fresh_grant.allowed
    }
    assign = _task_assign(
        task, cell_id, fresh_grant.id, terms.attempt, resume_from=terms.resume_from
    )
    await link.transport.send(wrap(fresh_grant.to_wire(sources), link.hop, clock=deps.clock))
    await link.transport.send(wrap(assign, link.hop, clock=deps.clock))
    return fresh_grant


async def _deny_zero_grant(
    deps: QueenDeps, task: Task, inputs: GrantInputs, fresh_grant: ForageGrant
) -> None:
    """Record `forage.denied` for a grant that can run no bee, and fail `task` at once.

    Two ways a grant runs no bee: it computed to zero sub-bees, or (roadmap step 10.3) the
    grant_issue point removed every model binding it named; `allowed_bindings` on the event and
    the outcome's own summary say which.

    Carries the allocator's own `reason` string (`hivemind.forage.allocate.grant`'s own
    `_reason`) plus the free-memory, reserve and seat figures that produced zero, so an operator
    reading the trail -- or `hive run`'s own streamed line -- never has to guess why. Capacity is
    probed once, at lease time (codingrules section 8.10: "grants are leases"), so nothing here
    waits for memory to free up; failing the task at once is the right v0 behaviour.
    """
    host = inputs.cell_capacity.host
    await record_forage_event(
        deps,
        "forage.denied",
        fresh_grant.id,
        task_id=task.id,
        warden_id=inputs.holder,
        cell_id=inputs.cell_id,
        max_sub_bees=fresh_grant.max_sub_bees,
        cell_cap=inputs.cell_capacity.max_sub_bees,
        free_memory_bytes=host.memory_free_bytes,
        reserve_memory_bytes=inputs.reserve.memory_bytes,
        reserve_seats=inputs.reserve.seats,
        footprint_memory_bytes=inputs.footprint.memory_bytes,
        allowed_bindings=len(fresh_grant.allowed),
        reason=fresh_grant.reason,
    )
    what = "zero sub-bees" if fresh_grant.max_sub_bees < 1 else "no model binding"
    summary = f"Forage denied: grant allows {what}. {fresh_grant.reason}"
    outcome = TaskOutcome(status=TaskStatus.FAILED, summary=summary)
    await deps.chamber.fail(task.id, outcome)


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
    """Record the one `forage.granted` ForageEvent no other module writes."""
    event = ForageEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="forage.granted",
        subject_id=fresh_grant.id,
        # max_sub_bees is on the trail so a grant that allows no bee at all is visible where the
        # assignment it covers would otherwise park silently (hivemind.wardens.ticks.assign).
        payload={
            "task_id": task.id,
            "warden_id": warden_id,
            "max_sub_bees": fresh_grant.max_sub_bees,
        },
    )
    await deps.trail.record(event)


def _task_assign(
    task: Task,
    cell_id: CellId,
    grant_id: GrantId,
    attempt: int,
    *,
    resume_from: HandoffRef | None = None,
) -> TaskAssign:
    """Build the TaskAssign a task's Warden receives, at `attempt`."""
    reason = (
        "Resumed by the Queen's dispatcher (Clustering)."
        if resume_from is not None
        else "Placed by the Queen's dispatcher."
    )
    return TaskAssign(
        task_id=task.id,
        goal_id=task.goal_id,
        cell_id=cell_id,
        role=WorkerRole.DRONE,
        slot=ModelSlot.WORKER.to_wire(),
        objective=task.spec.objective,
        acceptance=task.spec.acceptance,
        leaves=task.spec.leaves,
        # Waggle 1.6 (roadmap step 10.3): the goal's own ceiling and the task's network needs,
        # so the Warden attenuates its Worker's set to both.
        capabilities=task.spec.capabilities,
        network_scopes=task.spec.needs.network_scopes,
        tempo=task.spec.needs.tempo.to_wire(),
        clearance=task.spec.clearance.to_wire(),
        grant_id=grant_id,
        attempt=attempt,
        resume_from=resume_from,
        reason=reason,
    )


def _link_for(wardens: Sequence[WardenLink], warden_id: WardenId) -> WardenLink | None:
    """Return the WardenLink named by `warden_id`, or None when it names no attached Warden."""
    return next((link for link in wardens if link.warden_id == warden_id), None)


async def _ensure_warden_provisioned(deps: QueenDeps, link: WardenLink) -> None:
    """Set `link`'s first Ceilings and write its Cell's HostingPlan, once per attachment.

    `Queen.attach_warden` only records an already-built link, so this runs here instead, at the
    first dispatch that ever reaches this Warden. `deps.ledger.decisions.ceilings_for` already
    distinguishes "never set" (None) from "set once" for exactly this reason.
    """
    if deps.ledger.decisions.ceilings_for(link.warden_id) is not None:
        return  # Already provisioned on an earlier dispatch to this same Warden.
    await set_ceilings(link, _initial_ceilings(link.cell), deps)
    await write_hosting_plan(link.cell, deps, link)


def _initial_ceilings(cell: Cell) -> Ceilings:
    """Build a newly attached Warden's first Ceilings from its Cell's own capacity report.

    `max_sub_bees` is the Cell's own cap; VRAM and disk start at the Cell's own free figures, so a
    Warden can load a model at all before the Queen ever tightens either. Nothing is exported or
    allowlisted yet: the Queen raises `exportable_seats`/`loadable_sources` later, once a model is
    actually worth sharing or loading (codingrules section 8.10: "ceilings, not approvals").
    """
    free_vram = sum(gpu.vram_free_bytes for gpu in cell.capacity.host.gpus)
    return Ceilings(
        max_sub_bees=cell.capacity.max_sub_bees,
        model_vram_bytes=free_vram,
        model_disk_bytes=cell.capacity.host.disk_free_bytes,
        loadable_sources=(),
        exportable_seats=0,
    )
