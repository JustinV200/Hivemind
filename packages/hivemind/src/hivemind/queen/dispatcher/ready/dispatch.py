"""Define dispatch_ready, redispatch and resume_paused: place a task, then have it assigned.

Roadmap step 3.20's own dispatch map, now over `hivemind.queen.placement.decide`'s own three-way
`Placement` (roadmap step 5.7, ADR-0028): for each ready task (`hivemind.brood_chamber.
ready_tasks`), `hivemind.queen.dispatcher.snapshot` builds the pure `Inventory`/`ForageView`
snapshot `decide` reads, `decide` picks a `Placement`, `hivemind.queen.dispatcher.acquire.
resolve_link` turns it into a `WardenLink` (acquiring a Virtual Cell through the
`VirtualCellProvider` seam first, if it names one), `hivemind.queen.dispatcher.sizing.size_grant`
computes a fresh `ForageGrant` from the Cell's figures as they stand, `queen.placed` records the
placement's own reason (the wax that weighed on it included), `chamber.assign` then
`chamber.start` move the task PENDING -> ASSIGNED -> RUNNING, `queen.assigned` records the Cell it
landed on, and only then does the Warden receive a `GrantIssued` followed by a `TaskAssign` -- in
that order, so a Warden never sees an assignment its grant has not already arrived for
(`hivemind.wardens.ticks.assign.handle_assign`'s own park-until-both-arrive contract). The Queen
never assigns a Worker directly (codingrules section 8.8): every assignment goes to a Warden,
which is what actually spawns.

`redispatch` is the sibling `hivemind.queen.ticks.results.retry_task` and an alarm-driven REBIND
call for: a retry stays where the task is placed. A RUNNING task's edge back to PENDING
(`chamber.unassign` or `requeue_for_commands`) is a fresh placement, for a task whose Cell refused
its commands, whose device was lost or whose Hive stopped, never a retry, so a retry does not go
through it the way a fresh dispatch goes through `chamber.assign`. It resends a fresh grant and
assignment straight to the Cell and Warden the task is already placed on (no placement decision at
all), leaving the chamber's own `Task.status` at RUNNING throughout -- the same way a Warden's own
internal RETRY/REBIND never tells the Brood Chamber anything happened at all.

A grant that runs no bee is never sent (`hivemind.queen.dispatcher.ready.assign`'s own docstring has
the rule); what this half decides is when: the Hive Stand's own capacity is re-read on every
dispatch pass (`WardenLink.live_capacity`), so a fresh dispatch sizes its grant before the chamber
moves: a grant a passing shortfall zeroes (the Hive Stand's free cores or free memory right now)
leaves the task PENDING to be tried again on later passes, one `forage.denied` with `deferred =
true` saying so, and fails it with the figures only once `[forage] zero_grant_patience_s` has
passed; a goal whose other running tasks hold its whole allowance waits, before any Cell is chosen,
until one of them finishes; a lasting shortfall still fails the task at once
(`hivemind.queen.dispatcher.zero_grant`'s docstring has the rule). `redispatch` and `resume_paused`
(a RUNNING retry and a resumed PAUSED task) still fail at once on any zero: a retried or resumed
task stays placed rather than going back to PENDING, so it has no queue to wait in, and for the same
reason they size their grants from the link's own Cell as probed, never the live reading. A
placement no Cell can bear at all never reaches this point: `hivemind.queen.placement`'s own fit
rule refuses the Cell first (roadmap step 5.7).

A Cell the Queen is holding (`DispatchBook.held`: its Swarm device is unreachable and she waits
for it, ADR-0053) is neither a candidate for a fresh placement nor the target of a retry or a
resume: the dispatcher neither fails nor moves what is held, and acts on it again when the hold
ends. A task taken back from a device she gave up on goes through the ordinary placement above
again, with the Handoff she held for it (`DispatchBook.resumes`) carried in its assignment
(`.handoff`), inline when its new Warden cannot read her memory store.

One dispatch pass tries every ready task at most once, in `(created_at, id)` order, and a task
left PENDING (a placement failure, or a wait) no longer ends the pass for the tasks behind it: a
goal waiting on its own allowance must never hold up another goal's work.

The dispatcher lifecycle fix: a pass no longer awaits a Virtual Cell's provision. It authorizes a
Virtual placement (`acquire.authorize_virtual`), sizes the grant its Cell's spec promises
(`zero_grant.ready_to_provision`: a Cell whose grant could run no bee is never made), starts its
acquisition beside the tick (`hivemind.queen.dispatcher.provisions`, bounded) and moves on, the
task still PENDING; a later pass collects the Cell and places the task on it, and a Cell whose
task was cancelled or failed meanwhile, or whose grant was denied, is released rather than left
behind, that release too running beside the tick (`hivemind.queen.dispatcher.releases`, whose
ended releases every pass collects). Every pass first returns the grants of every task that has
ended to the pool (`hivemind.queen.forage.grants.release_finished`), so the ledger, and the
placement snapshot that now reads the grants in force on each Cell, never count finished work.
Busy seats wait like a goal's allowance, before any Cell is chosen (`zero_grant.hold_for_room`). A
task no Cell can take is said to wait once per cause (`queen.decided`, `DispatchBook.unplaced`),
not on every pass.

A task is ASSIGNED only between `chamber.assign` and `chamber.start` inside one pass, which
holds the dispatch lock, and nothing is sent to its Warden before `start`. So a task any pass
finds ASSIGNED was stranded there: the Queen stopped, or the pass failed, between the two writes,
and its Warden never heard of it. Every pass gives such a task back to PENDING through
`chamber.unassign` (the Brood Chamber's ASSIGNED -> PENDING edge, which had no production caller
until now; phase-7 handoff, section 8 item 5), so it is placed afresh as a new attempt rather than
left ASSIGNED for good, with its goal never finishing. Every Warden a task is placed on is noted
in `DispatchBook.placed_on` first, so a stopping Hive gives back exactly the tasks it left on its
own Cells (`hivemind.queen.dispatcher.ready.unfinished`).

Roadmap steps 10.3a-c add the tiers: a Night Veil task meets the tier's floors at the placement
point before any Cell is chosen (`hivemind.queen.dispatcher.night_veil`), a final
`PlacementError` (a Night Veil rule broken for good) cancels the task with its reason at once,
`chamber.assign` binds the task to its Cell's tier, and `queen.placed` names the goal request a
placement stands on.

Roadmap step 10.3 (ADR-0039) wires the `placement` enforcement point through here: a
`PlacementError` records its own message on `queen.decided`, and when the task's goal set alone
left no candidate (`PlacementError.denied`) the Queen, acting for the goal, checks each missing
capability through her `Enforcer`, which records the `guard.denied`. Everything from the grant on
(the `grant_issue` point, the Honey pre-check, the wire sends) is `.assign`'s.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher.ready`
    sub-package. Called unconditionally at the end of every `hivemind.queen.queen.Queen` tick, and
    once more immediately after `submit_goal` and after every `COMPLETE_TASK` decision, so a
    newly-ready task is placed without waiting for the next tick; `redispatch` is called by
    `hivemind.queen.ticks.results.retry_task` and `hivemind.queen.ticks.alarms` for a REBIND.
    Calls into `hivemind.brood_chamber`, `hivemind.guard` (the placement point),
    `hivemind.pheromone`, `hivemind.queen.authority`, `hivemind.queen.deps`,
    `hivemind.queen.forage.grants` (release_finished), `hivemind.queen.placement`,
    `hivemind.queen.dispatcher.acquire`/`.night_veil`/`.provisions`/`.sizing`/`.snapshot`/
    `.zero_grant` and `.ready.assign`, `hivemind.queen.trail` (record_event) and waggle only.

Key invariants:
    - A `PlacementError` for one ready task never stops the others: `dispatch_ready` records the
      failure on the trail and moves on to the next ready task, bounded to at most one attempt per
      task id per call so it can never loop forever on a task that will never fit.
    - `chamber.assign`/`chamber.start` run only for a fresh dispatch, never for `redispatch`: a
      RUNNING task's own status is untouched by a retry.
    - `queen.placed` is always recorded before `queen.assigned`, for a fresh dispatch: the reason a
      Cell was chosen precedes the record that it was actually assigned.
    - Nothing reaches a Warden for a fresh dispatch before the chamber reads RUNNING: the grant is
      sized before the transition, and every wire send (ceilings, hosting plan, grant, assignment)
      follows it.
    - One dispatch pass tries each ready task at most once, and a task it leaves PENDING never
      stops it from trying the ready tasks behind it.
    - No pass awaits a Virtual Cell's provision; a Cell acquired for a task is placed for it on a
      later pass, or released once the task is gone or its grant denied.
    - A pass releases the grants of every ended task before it places anything.
    - A held Cell is never chosen for a fresh placement, and `redispatch` and `resume_paused` send
      nothing to it while it is held.
    - A pass first unassigns every task it finds ASSIGNED: no task stays ASSIGNED past the pass
      that assigned it.
    - A pass sends every attached Warden still owed its ceilings or hosting plan (a first send
      its link refused) both again before it assigns anything (`hivemind.queen.forage.
      deliveries`).
    - `guard.denied` for placement is recorded only when the goal's capability set alone left no
      candidate; a capacity or fit failure records `queen.decided` and nothing more, once per
      cause for as long as its task waits PENDING.

See Also:
    - .claude/roadmap.md step 5.7 for "records queen.placed with the reason, the wax that weighed
      on it included".
    - .claude/codingrules.md section 8.8 for "never assigns to a Worker directly".
    - hivemind.queen.placement for decide, this module's one placement call.
    - hivemind.queen.dispatcher.acquire for resolve_link, this module's Placement-to-Warden call.
    - hivemind.queen.dispatcher.provisions for the lane a Virtual Cell is acquired in.
    - hivemind.queen.dispatcher.sizing for size_grant, this module's one allocation call.
    - hivemind.queen.dispatcher.zero_grant for the wait-or-deny rule a zero grant follows.
    - hivemind.queen.dispatcher.ready.assign for what happens once a task has its Cell and grant.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import JsonValue

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus, is_terminal, ready_tasks
from hivemind.guard import CapabilitySet, EnforcementPoint
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS
from hivemind.queen.authority import goal_held, request_for
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.acquire import authorize_virtual, resolve_link
from hivemind.queen.dispatcher.night_veil import check_night_veil_placement, tier_context
from hivemind.queen.dispatcher.provisions import (
    GRANT_DENIED,
    acquired,
    acquired_for_task,
    forget_provision,
    provisioning,
    release_cell,
    settle_provisions,
    start_provision,
)
from hivemind.queen.dispatcher.ready.assign import (
    AssignmentTerms,
    record_forage_granted,
    send_grant_and_assign,
)
from hivemind.queen.dispatcher.ready.handoff import resume_plan
from hivemind.queen.dispatcher.releases import collect_releases
from hivemind.queen.dispatcher.sizing import size_grant
from hivemind.queen.dispatcher.snapshot import build_forage_view, build_inventory
from hivemind.queen.dispatcher.zero_grant import (
    forget_waits,
    hold_for_room,
    ready_to_provision,
    settle_wait,
)
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.forage import redeliver_owed
from hivemind.queen.placement import (
    Placement,
    PlacementError,
    ProvisionVirtual,
    ReuseReal,
    decide,
)
from hivemind.queen.trail import record_event
from waggle.ids import TaskId, WardenId
from waggle.messages import HandoffRef
from waggle.messages.task import NurseFinding

# The reason `task.unassigned` records for a task a pass found stranded ASSIGNED: ids and plain
# words only, never task content (codingrules section 12).
STRANDED_REASON = "Found assigned but never started; its Warden was sent nothing."

__all__ = ["STRANDED_REASON", "dispatch_ready", "redispatch", "resume_paused"]


async def dispatch_ready(deps: QueenDeps, wardens: Sequence[WardenLink]) -> None:
    """Place, grant and assign every ready task, each tried at most once this call.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached; placement picks among these plus any Virtual
            side `deps.virtual_backends`/`.dormant_cells` names.

    Returns:
        None, once every ready task has been dispatched, found unplaceable, or left to wait.
    """
    # One dispatch pass at a time per Queen: Queen.submit_goal, a finished task and the tick all
    # call this, and two passes interleaving would both see the same PENDING task and one would
    # lose the chamber's own PENDING -> ASSIGNED transition (DispatchBook.lock's own docstring).
    async with deps.dispatch.lock:
        attempted: set[TaskId] = set()
        tasks = await _unassign_stranded(deps, await deps.chamber.list(TaskFilter()))
        # A Warden whose ceilings or plan never reached it gets them before any new assignment.
        await redeliver_owed(deps, wardens)
        # A task that has ended gives its grants back first, so this very pass sees their room.
        ended = {task.id for task in tasks if is_terminal(task.status)}
        await forage_grants.release_finished(deps.ledger, deps, ended)
        ready = ready_tasks(tasks)
        deps.dispatch.queue_depth = len(ready)  # Swarming's queue depth (ADR-0063).
        # A task that left PENDING some other way (its goal cancelled, say) waits no longer, and a
        # Cell acquired for it is released rather than left behind.
        forget_waits(deps, {task.id for task in ready})
        await settle_provisions(deps, {task.id for task in ready})
        # A Cell whose device the Queen is holding is not placed on until it is back (ADR-0053).
        wardens = [link for link in wardens if not deps.dispatch.held.is_held(link.cell.id)]
        # A release that has ended leaves the lane; its Cell's room is the lifecycle's again.
        await collect_releases(deps)
        # Each ready task once, earliest first; one left PENDING (unplaceable, waiting for a
        # grant, or its Cell still being made) is skipped rather than ending the pass, so it never
        # holds up the tasks behind it.
        while (task := _first_untried(ready, attempted)) is not None:
            attempted.add(task.id)
            try:
                await _dispatch_one(deps, wardens, task)
            except PlacementError as exc:
                # This one stays PENDING to retry on a later pass, unless its goal's own set
                # excluded every Cell, which cancels it for good.
                await _record_placement_failure(deps, task, exc)
            # Read again: what was just dispatched, failed or cancelled is no longer ready.
            ready = await _ready_tasks(deps)
            deps.dispatch.queue_depth = len(ready)


async def redispatch(
    deps: QueenDeps,
    wardens: Sequence[WardenLink],
    task_id: TaskId,
    attempt: int,
    review_findings: tuple[NurseFinding, ...] = (),
) -> None:
    """Resend a fresh grant and assignment to a RUNNING task's own Cell and Warden (a retry).

    Never touches the task's own chamber status (module docstring): a retry is a wire-level
    re-send to wherever the task is already placed, not a fresh placement decision, which is
    what RUNNING's edge back to PENDING is for.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        task_id: The task to retry; must already be placed (have a `warden_id` and `cell_id`).
        attempt: The attempt number to stamp on the fresh `TaskAssign`.
        review_findings: A Nurse Bee's findings for the fresh Worker to fix forward (ADR-0061);
            empty for every other retry.

    Returns:
        None, once a fresh grant and assignment have been sent; silently if the task is somehow
        not placed or its Warden is no longer attached (nothing to resend to); or having instead
        denied the grant and failed the task if it runs no bee, at once whatever the cause: a
        RUNNING task has no PENDING queue to wait in, which is also why its grant is sized from
        the link's own Cell as probed, never the live reading (module docstring).
    """
    task = await deps.chamber.get(task_id)
    link = _placed_link(deps, wardens, task)
    if link is None:
        return  # Not placed, held, or its Warden is gone: there is nothing to resend to.
    sized = await size_grant(deps, link, task, read_live=False)
    terms = AssignmentTerms(attempt=attempt, review_findings=review_findings)
    fresh_grant = await send_grant_and_assign(deps, link, task, terms, sized)
    if fresh_grant is None:
        return  # Denied: send_grant_and_assign already failed the task (module docstring).
    await record_forage_granted(deps, task, fresh_grant, link.warden_id)


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
        now-RUNNING task if it runs no bee, at once whatever the cause, and sized from the link's
        own Cell as probed, exactly as `redispatch` does (a resumed task, `resume_from` included,
        has no PENDING queue to wait in either).
    """
    task = await deps.chamber.get(task_id)
    link = _placed_link(deps, wardens, task)
    if link is None:
        return  # Not placed, held (it stays paused until the hold ends), or its Warden is gone.
    await deps.chamber.resume(task_id, reason)
    # A fresh bee (TaskAssign.resume_from's own field docstring: "a fresh one for a fresh bee");
    # the Queen owns the attempt number on every Queen-to-Warden hop. A Warden that cannot read
    # her memory store is sent the Handoff itself (`.handoff`) when she holds it, and the
    # reference alone otherwise, for it to read from its own.
    plan = await resume_plan(deps, link, task, resume_from)
    terms = AssignmentTerms(
        attempt=task.attempt + 1, resume_from=plan.resume_from, handoff=plan.handoff
    )
    sized = await size_grant(deps, link, task, read_live=False)
    fresh_grant = await send_grant_and_assign(deps, link, task, terms, sized)
    if fresh_grant is None:
        return  # Denied: send_grant_and_assign already failed the task (module docstring).
    await record_forage_granted(deps, task, fresh_grant, link.warden_id)


async def _dispatch_one(deps: QueenDeps, wardens: Sequence[WardenLink], task: Task) -> None:
    """Place, grant and assign one ready task, in that order, or leave it PENDING to wait.

    The grant is sized BEFORE the chamber's own PENDING -> ASSIGNED -> RUNNING transition, so a
    grant a passing shortfall zeroes leaves the task PENDING with nothing recorded but its wait's
    one event and nothing sent (`zero_grant.settle_wait`). Every wire message, the Warden's first
    ceilings and hosting plan included, is sent only AFTER that transition and `queen.assigned`:
    a real Warden reacting to `TaskAssign` can, on genuine SQLite I/O, run faster than the Queen's
    own remaining bookkeeping -- and a Drone's own immediate Question forwarded straight back up
    (`Queen._act`'s `BLOCK_ON_QUESTION` handling) must never find the chamber still reading
    ASSIGNED while it tries to move a RUNNING task to BLOCKED.
    """
    if provisioning(deps, task.id):
        return  # Its Cell is still being made beside the tick: never awaited here.
    # Roadmap steps 10.3a/c: a Night Veil task meets the tier's floors before any Cell is chosen.
    await check_night_veil_placement(deps, task)
    # A goal whose running tasks hold its whole allowance, or a task whose every seat is busy,
    # waits before any Cell is chosen: a Cell acquired now would only sit idle.
    if await hold_for_room(deps, task):
        return
    placed = await _place(deps, wardens, task)
    if placed is None:
        return  # A Cell is being acquired for it beside the tick (or will be): still PENDING.
    link, placement = placed
    sized = await settle_wait(deps, task, await size_grant(deps, link, task))
    if sized is None:
        return  # Waiting for room: still PENDING, nothing sent; a Cell acquired for it stays.
    await _record_placed(deps, task, placement)
    await _start_on(deps, task, link)
    # The Cell is the running task's own from here on; until then a task that left PENDING under
    # this pass (cancelled meanwhile) still leaves the Cell in the lane, released by the next one.
    forget_provision(deps, task.id)
    fresh_grant = await send_grant_and_assign(
        deps, link, task, await _placed_terms(deps, link, task), sized
    )
    if fresh_grant is None:
        # Denied, the task already failed: a Cell acquired for it alone would never be used.
        if acquired_for_task(placement):
            await release_cell(deps, await deps.chamber.get(task.id), link, GRANT_DENIED)
        return
    # "placed" and "assigned" (both just above) precede "granted" on the trail.
    await record_forage_granted(deps, task, fresh_grant, link.warden_id)


async def _placed_terms(deps: QueenDeps, link: WardenLink, task: Task) -> AssignmentTerms:
    """Return the terms of a fresh placement: a task taken back from a lost device resumes."""
    # Taken once: the hint was noted when the Queen gave up on the device, and this is the
    # placement that follows (`DispatchBook.resumes`); any other task has none and starts fresh.
    plan = await resume_plan(deps, link, task, deps.dispatch.resumes.take(task.id))
    return AssignmentTerms(attempt=task.attempt, resume_from=plan.resume_from, handoff=plan.handoff)


async def _unassign_stranded(deps: QueenDeps, tasks: tuple[Task, ...]) -> tuple[Task, ...]:
    """Give every task stranded ASSIGNED back to PENDING; return `tasks` as they now stand.

    Only ever called under the dispatch lock, where no pass is between its `assign` and `start`,
    so every ASSIGNED task here is one whose Warden was never sent anything (module docstring).
    """
    stranded = {task.id for task in tasks if task.status is TaskStatus.ASSIGNED}
    if not stranded:
        return tasks
    for task_id in stranded:
        # A new attempt: the next assign re-binds it to whichever Cell it lands on.
        await deps.chamber.unassign(task_id, STRANDED_REASON)
    return tuple([await deps.chamber.get(t.id) if t.id in stranded else t for t in tasks])


async def _start_on(deps: QueenDeps, task: Task, link: WardenLink) -> None:
    """Move `task` PENDING -> ASSIGNED -> RUNNING on `link`'s Cell, and record `queen.assigned`."""
    # Hers from before the first write: a stop gives back whatever is still placed on it, even a
    # task this very pass leaves stranded between the two writes (ready.unfinished). A Warden on a
    # colonized device keeps working through a stop (ADR-0053), so its tasks are never given back.
    if not link.outlives_the_hive:
        deps.dispatch.placed_on.add(link.warden_id)
    # Roadmap step 10.3b: the task is bound to its Cell's tier with the assignment, so every later
    # check for it (a rebind, a grant revision, an egress change) reads the tier it runs under.
    await deps.chamber.assign(
        task.id,
        link.warden_id,
        link.cell.id,
        "Placed by the Queen's dispatcher.",
        bound_tier=link.cell.comb_shield,
    )
    await deps.chamber.start(task.id)
    await record_event(
        deps, "queen.assigned", task.id, cell_id=link.cell.id, warden_id=link.warden_id
    )


async def _place(
    deps: QueenDeps, wardens: Sequence[WardenLink], task: Task
) -> tuple[WardenLink, Placement] | None:
    """Return the Cell `task` goes to and why, or None while a Cell is acquired for it.

    A Cell already acquired for it is its Cell. Otherwise placement decides: an attached Cell is
    returned at once; a Virtual one is authorized on this pass, so a refusal is recorded now,
    and its grant sized from the Cell's spec (`zero_grant.ready_to_provision`: the task waits, or
    is refused, rather than have a Cell made it could not use), then acquired beside the tick,
    where no pass ever awaits it (`provisions.start_provision`; a full lane leaves the task for a
    later pass to start).

    Raises:
        PlacementError: No Cell fits, the Virtual Cell was refused, or its acquisition, collected
            on this pass, failed.
    """
    placed = acquired(deps, task.id)
    if placed is not None:
        return placed
    inventory = await build_inventory(deps, wardens, task=task)
    placement = decide(
        task.spec.needs, inventory, build_forage_view(deps, task), deps.placement_policy
    )
    if isinstance(placement, ReuseReal):
        return await resolve_link(deps, wardens, task, placement)
    await authorize_virtual(deps, task, placement)
    # Sized before any Cell is made: one whose grant could run no bee is never provisioned.
    if await ready_to_provision(deps, task, placement):
        start_provision(deps, wardens, task, placement)
    return None


async def _record_placement_failure(deps: QueenDeps, task: Task, error: PlacementError) -> None:
    """Record why `task` found no Cell, and refuse each capability its goal alone lacked.

    `queen.decided` carries the error's own message (every rule that eliminated a candidate),
    bounded to the trail's per-string limit. A task left waiting is said to be once per cause,
    exactly as a grant's wait is: the row is recorded when its wait begins or its cause changes,
    never on every pass it keeps waiting (a full lone Cell used to add one row per task per
    tick), and `zero_grant.forget_waits` ends the wait once the task leaves PENDING. When the
    goal's set alone left no candidate (`error.denied`, roadmap step 10.3), the Queen acting for
    the goal checks each missing capability at the placement point, so each is a `guard.denied`
    row with its reason, and then cancels the task: a goal's set is fixed for its whole life, so
    no later pass could place it, and leaving it PENDING would re-record the same refusals.
    """
    detail = str(error)[:MAX_PAYLOAD_STRING_CHARS]
    # The message names every rule that eliminated a candidate and no passing figure, so the same
    # text is the same cause: already said, so said no more.
    if deps.dispatch.unplaced.get(task.id) != detail:
        deps.dispatch.unplaced[task.id] = detail
        await record_event(deps, "queen.decided", task.id, reason="placement_failed", detail=detail)
    if error.final and not error.denied:
        # Roadmap step 10.3a: a Night Veil rule broken for good, refused once and loudly; its
        # guard.denied, when a floor refused, is already on the trail.
        await deps.chamber.cancel(task.id, detail)
        return
    # Capacity and fit failures are transient (a Cell frees up, a backend returns): only a goal
    # ceiling that excludes every candidate is final.
    if not error.denied:
        return
    held = goal_held(task) or CapabilitySet.empty()  # `denied` is only ever set for a goal set.
    # With the Night Veil facts when they apply: without the goal request it cites, the
    # initiation floor would refuse each check as if the task cited none, and name the wrong rule.
    context = await tier_context(deps, task)
    for needed in error.denied:
        request = request_for(deps, EnforcementPoint.PLACEMENT, needed, held)
        await deps.enforcer.check(request.model_copy(update={"context": context}))
    lacking = ", ".join(str(needed) for needed in error.denied)
    reason = f"The goal's capability set admits no Cell: it lacks {lacking}."
    # PENDING -> CANCELLED is the chamber's edge for "the Queen cancels the goal"; the reason is
    # task.cancelled's own, so the operator reads why without searching the trail.
    await deps.chamber.cancel(task.id, reason[:MAX_PAYLOAD_STRING_CHARS])


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
    if task.spec.goal_request_id is not None:
        # ADR-0039: the durable request a placement stands on (a Night Veil one's human ask).
        payload["goal_request_id"] = task.spec.goal_request_id
    await record_event(deps, "queen.placed", task.id, **payload)


def _placed_link(deps: QueenDeps, wardens: Sequence[WardenLink], task: Task) -> WardenLink | None:
    """Return the attached link `task` is placed on, or None when it has none to be sent through.

    None when the task is not placed (a RUNNING or PAUSED one always is, Clustering never
    unassigns), when its Cell is held (its device is unreachable and the Queen waits for it,
    ADR-0053), or when its Warden is no longer attached.
    """
    if task.warden_id is None or task.cell_id is None:
        return None
    if deps.dispatch.held.is_held(task.cell_id):
        return None
    return _link_for(wardens, task.warden_id)


def _link_for(wardens: Sequence[WardenLink], warden_id: WardenId) -> WardenLink | None:
    """Return the WardenLink named by `warden_id`, or None when it names no attached Warden."""
    return next((link for link in wardens if link.warden_id == warden_id), None)


async def _ready_tasks(deps: QueenDeps) -> tuple[Task, ...]:
    """Return every task ready to dispatch, earliest first (`chamber.next_ready`'s own order)."""
    return ready_tasks(await deps.chamber.list(TaskFilter()))


def _first_untried(ready: Sequence[Task], attempted: set[TaskId]) -> Task | None:
    """Return the earliest task in `ready` this pass has not tried yet, or None."""
    return next((task for task in ready if task.id not in attempted), None)
