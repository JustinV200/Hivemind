"""Define send_grant_and_assign: check, book and send one placed task's grant, then its TaskAssign.

Every dispatch path (`hivemind.queen.dispatcher.ready.dispatch`'s `dispatch_ready`, `redispatch`
and `resume_paused`) ends here once its task has a Cell and a sized grant: `send_grant_and_assign`
is the one choke point a grant and an assignment pass on their way to a Warden, in that order --
`GrantIssued` then `TaskAssign`, on the same link, so a Warden never sees an assignment its grant
has not already arrived for (`hivemind.wardens.ticks.assign.handle_assign`'s own
park-until-both-arrive contract). The Queen never assigns a Worker directly (codingrules section
8.8): every assignment goes to a Warden, which is what actually spawns.

It is also the one choke point that used to send a grant computing to `max_sub_bees == 0` (a Cell so
out of headroom that not even one Drone fits) exactly like every other grant: the Warden then raised
`GRANT_EXCEEDED` ("allows zero sub-bees"), the Queen escalated that to the human inbox, and the task
sat RUNNING until its whole timeout elapsed with no Drone and nothing in `hive run`'s own view
saying why (`.claude/phase-4-handoff.md` section 4.2 item 1, found for real on 2026-09-20 when low
free RAM sized a grant to zero). A grant that runs no bee is still never sent: it is recorded as
`forage.denied` (the allocator's own reason plus the core, load, memory, reserve and seat figures
that produced zero) and the task fails with that reason as its outcome summary
(`hivemind.queen.dispatcher.zero_grant.deny_zero_grant`).

Roadmap step 10.3 (ADR-0039) adds the `grant_issue` enforcement point: every fresh grant passes
`hivemind.queen.dispatcher.grants.authorize_grant` before it is sent, and one left with no model
binding is refused at once like a lasting zero-bee grant (`forage.denied`, the task failed). The
`TaskAssign` carries the goal's capability set (Waggle minor 8) and the task's Exoskeleton need and
network scopes (Waggle minor 6), so the Warden attaches exactly what the task needs and attenuates
its Worker's set to both.

`_task_assign` (roadmap steps 6.9/6.10) reads `task.spec.role` for the wire `TaskAssign.role`
directly, rather than the DRONE every task used to get regardless of what it was planned as, and
carries `recon`: the `waggle.messages.task.ScoutReport`s of the task's SUCCEEDED Scout
dependencies (`_recon_for`), so a Forager's brief is built from what a Scout it depended on
already found. `hivemind.queen.dispatcher.sizing` sizes the fresh grant against the role's own
`[forage.roles]` footprint when the manifest set one, else the Drone's -- `forager`/`scout` are
never required manifest keys. Both `_task_assign` and `size_grant` are the one choke point every
dispatch path (`dispatch_ready`, `redispatch`, `resume_paused`) funnels through, so a role and its
recon are carried the same way regardless of which one sent the assign.

Every assignment also carries what the Queen's Honey pre-check found (roadmap steps 7.9 and 7.9a,
`hivemind.queen.dispatcher.honey.consult_for_assignment`): Honey about the task's objective and
the chosen Cell's own history, plus that Cell's live Cell Wax, on `TaskAssign.honey`. The
pre-check runs inside `send_grant_and_assign`, once the grant is known to be sendable, so a fresh
dispatch, a retry and a resume all get one; it never raises, and yields no hits when it fails.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the `queen.dispatcher.ready`
    sub-package. Called by `.dispatch` only. Calls into `hivemind.brood_chamber` (Task,
    TaskOutcome, TaskStatus), `hivemind.cell` (Cell), `hivemind.forage` (Ceilings, ForageGrant,
    ModelSlot), `hivemind.pheromone` (ForageEvent), `hivemind.queen.deps` (QueenDeps, WardenLink),
    `hivemind.queen.dispatcher.grants`/`.honey`/`.sizing`/`.zero_grant`,
    `hivemind.queen.forage.grants`/`.ceilings`/`.hosting` and waggle (including
    `waggle.messages.task.recon.MAX_RECON_REPORTS`) only.

Key invariants:
    - `GrantIssued` is always sent before `TaskAssign`, on the same Warden link, for the same task,
      whether from a fresh dispatch or a retry.
    - A grant with `max_sub_bees < 1`, or with no model binding left once `authorize_grant` has
      run, is never sent to a Warden: `send_grant_and_assign` records `forage.denied` and fails
      the task (RUNNING -> FAILED) instead, whether the grant came from a fresh dispatch, a retry
      or a resume (a fresh dispatch's passing shortfall never reaches here: `.dispatch` waits it
      out first).
    - `_recon_for` never returns more than `MAX_RECON_REPORTS` reports, and only from the task's
      own direct dependencies (never the transitive graph), ordered newest completion first.
    - The Honey pre-check never stops an assignment: it runs only for a grant that will be sent,
      and a failed or unwired pre-check leaves `TaskAssign.honey` empty.
    - A grant or assignment that never reaches its Warden's link fails the task at once, its grant
      revoked, rather than leaving it RUNNING with no Worker (`_send_or_fail`).

See Also:
    - .claude/roadmap.md steps 6.9 and 6.10 for the Forager and Scout roles this module assigns.
    - hivemind.queen.dispatcher.zero_grant for deny_zero_grant, the refusal a zero grant gets.
    - waggle.messages.task.recon for ScoutReport and MAX_RECON_REPORTS, and hivemind.brood_chamber.
      task.model.TaskOutcome.scout_report, the field `_recon_for` reads.
    - hivemind.queen.dispatcher.honey for consult_for_assignment, the pre-check every assignment
      carries.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, replace
from datetime import datetime

from hivemind.brood_chamber import Task, TaskOutcome, TaskStatus
from hivemind.cell import Cell
from hivemind.forage import Ceilings, ForageGrant, ModelSlot
from hivemind.pheromone import ForageEvent
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher.grants import authorize_grant
from hivemind.queen.dispatcher.honey import consult_for_assignment
from hivemind.queen.dispatcher.sizing import SizedGrant
from hivemind.queen.dispatcher.zero_grant import deny_zero_grant
from hivemind.queen.forage import grants as forage_grants
from hivemind.queen.forage.ceilings import set_ceilings
from hivemind.queen.forage.hosting import write_hosting_plan
from waggle.envelope import wrap
from waggle.ids import CellId, GrantId, WardenId, new_event_id
from waggle.messages import HandoffRef
from waggle.messages.forage import GrantIssued
from waggle.messages.forage.values import RevocationCause
from waggle.messages.honey import HoneyHit
from waggle.messages.task import ScoutReport, TaskAssign, WorkerRole
from waggle.messages.task.recon import MAX_RECON_REPORTS

__all__ = ["AssignmentTerms", "record_forage_granted", "send_grant_and_assign"]


@dataclass(frozen=True, slots=True)
class AssignmentTerms:
    """Bundles what one `TaskAssign` needs beyond `task` itself, within codingrules 5.1's limit.

    Attributes:
        attempt: The attempt number to stamp on the fresh TaskAssign.
        resume_from: The Handoff to resume from (roadmap step 4.9's own `resume_paused`); None
            for a fresh dispatch or an ordinary retry (`dispatch_ready`/`redispatch`'s own calls).
        recon: The Scout reports to carry (roadmap step 6.10, `_recon_for`); empty here, since
            none of this dataclass's three callers has the async chamber access `_recon_for`
            needs -- `send_grant_and_assign` fills it in with `dataclasses.replace` right before
            building the `TaskAssign`, the one place in this module that awaits it.
    """

    attempt: int
    resume_from: HandoffRef | None = None
    recon: tuple[ScoutReport, ...] = ()


async def send_grant_and_assign(
    deps: QueenDeps,
    link: WardenLink,
    task: Task,
    terms: AssignmentTerms,
    sized: SizedGrant,
) -> ForageGrant | None:
    """Check a sized grant, record it live in the ledger, and send it then a TaskAssign.

    Returns None instead, having denied the grant and failed `task`, when the grant runs no bee
    (module docstring): a grant that empty can run no Drone at all, so it is
    never sent. For a fresh dispatch `sized` is one `zero_grant.settle_wait` did not hold. Returns
    None too, having revoked the grant and failed `task`, when either wire message never reached
    this Warden's own link (`_send_or_fail`'s own docstring).
    """
    cell_id = link.cell.id
    # Roadmap step 4.8's own wiring step: the first dispatch ever sent to a Warden sets its
    # ceilings and writes its Cell's hosting plan first (hivemind.wardens.ticks.control handles
    # both on arrival); every later dispatch to the same Warden is a no-op here.
    await _ensure_warden_provisioned(deps, link)
    # The one choke point (module docstring): sending this anyway is what
    # used to park the task RUNNING until its whole timeout elapsed, with nothing on screen saying
    # why (`.claude/phase-4-handoff.md` section 4.2 item 1).
    if sized.grant.max_sub_bees < 1:
        await deny_zero_grant(deps, task, sized)
        return None
    # Roadmap step 10.3's grant_issue point: a binding neither set allows never leaves; a grant
    # with none left can run no bee at all, so it is refused the same way a zero-bee one is.
    fresh_grant = await authorize_grant(deps, link, task, sized.grant)
    if not fresh_grant.allowed:
        await deny_zero_grant(deps, task, dataclasses.replace(sized, grant=fresh_grant))
        return None
    await _book_grant(deps, cell_id, sized, fresh_grant)
    # Roadmap step 6.10: every SUCCEEDED Scout this task directly depends on, newest first, so a
    # Forager's brief carries what recon already found. Recomputed on every send (fresh dispatch,
    # redispatch or resume) since a dependency the task was placed against never changes once
    # terminal, so recomputing costs a few cheap chamber reads and never disagrees with itself.
    recon = await _recon_for(deps, task)
    # Roadmap 7.9/7.9a: what the Hive already knows about this task and Cell; never raises, and
    # bounded by its own timeout, so the assignment always goes out.
    honey = await consult_for_assignment(deps, task, link)
    assign = _task_assign(task, cell_id, fresh_grant.id, replace(terms, recon=recon), honey=honey)
    message = await forage_grants.grant_message(deps, fresh_grant)
    pending = _PendingSend(fresh_grant=fresh_grant, grant_issued=message, assign=assign)
    if not await _send_or_fail(deps, link, task, pending):
        return None
    return fresh_grant


async def _book_grant(
    deps: QueenDeps, cell_id: CellId, sized: SizedGrant, fresh_grant: ForageGrant
) -> None:
    """Record `fresh_grant` live in the ledger and let a Virtual Cell's lifecycle see it granted."""
    # roadmap step 4.7: the ledger is the live book of every shared grant, not only the ones a
    # ForageRequest later grows; activate() moves it past ISSUED since a task dispatch means the
    # Warden is about to draw on it at once. The Cell's capacity is reported here too, the very
    # reading the grant was sized from, so the ledger's headroom has real, current figures.
    await deps.ledger.report_capacity(cell_id, sized.inputs.cell_capacity)
    await deps.ledger.record_grant(forage_grants.activate(fresh_grant))
    # A Virtual Cell's own READY -> GRANTED edge (hivemind.hive.cell_state) is driven here, by the
    # dispatch that grants it, so `cell.granted` precedes the assignment on the trail; the seam is
    # a no-op for a Cell the lifecycle does not track (QueenDeps.on_cell_granted's own comment).
    if deps.on_cell_granted is not None:
        await deps.on_cell_granted(cell_id, fresh_grant.id)


@dataclass(frozen=True, slots=True)
class _PendingSend:
    """What `_send_or_fail` sends: the fresh grant, its wire message and the built assignment."""

    fresh_grant: ForageGrant
    grant_issued: GrantIssued
    assign: TaskAssign


async def _send_or_fail(
    deps: QueenDeps, link: WardenLink, task: Task, pending: _PendingSend
) -> bool:
    """Send the grant then the assignment; fail `task` and report False if either never arrives.

    By this point the task is already RUNNING in the chamber (module docstring: "assign/start
    land before either wire message is sent"), so a send that never reaches this Warden would
    otherwise leave it RUNNING with no Worker and nothing but liveness's own much slower,
    task-blind CELL_UNREACHABLE Alarm to ever notice it (no code path returns a RUNNING task to
    PENDING today: only the narrower ASSIGNED -> PENDING edge exists, and this task has already
    left ASSIGNED). Fail it now instead, the same way `deny_zero_grant` already fails an
    unsendable assignment.
    """
    if not await link.send(wrap(pending.grant_issued, link.hop, clock=deps.clock)):
        await _deny_unreachable_link(deps, link, task, pending.fresh_grant)
        return False
    if not await link.send(wrap(pending.assign, link.hop, clock=deps.clock)):
        await _deny_unreachable_link(deps, link, task, pending.fresh_grant)
        return False
    return True


async def _deny_unreachable_link(
    deps: QueenDeps, link: WardenLink, task: Task, fresh_grant: ForageGrant
) -> None:
    """Revoke the grant and fail `task` when either wire message could not reach this Warden.

    The grant is already ACTIVE in the ledger (`send_grant_and_assign` booked it before
    sending), so it is revoked here as HOLDER_OFFLINE rather than left to the expiry sweep: the
    ledger's headroom and the trail then agree at once that nobody holds it. `activate` is pure,
    so re-deriving the ACTIVE copy of `fresh_grant` gives exactly the row the ledger holds.
    """
    await forage_grants.revoke(
        deps.ledger,
        deps,
        forage_grants.activate(fresh_grant),
        RevocationCause.HOLDER_OFFLINE,
        "warden_link_closed",
    )
    summary = "The Warden's link closed before its grant or assignment could be delivered."
    outcome = TaskOutcome(status=TaskStatus.FAILED, summary=summary)
    await deps.chamber.fail(task.id, outcome)


async def _recon_for(deps: QueenDeps, task: Task) -> tuple[ScoutReport, ...]:
    """Return the ScoutReports of `task`'s SUCCEEDED Scout dependencies, newest completion first.

    Roadmap step 6.10: a Forager's brief carries what a Scout it depended on already found, so it
    never rediscovers the same ground. Bounded to MAX_RECON_REPORTS; a dependency that is not a
    SUCCEEDED Scout, or whose outcome carries no report, contributes nothing -- a task with no
    Scout dependency (every Drone today) gets an empty tuple back.

    Args:
        deps: The Queen's collaborators; `deps.chamber.get` is the one call this makes, once per
            direct dependency.
        task: The task about to be assigned; only its direct `spec.depends_on` are read, never
            the transitive graph.

    Returns:
        At most MAX_RECON_REPORTS ScoutReports, ordered by the reporting task's own `updated_at`
        descending (the most recently completed Scout first).
    """
    dated: list[tuple[datetime, ScoutReport]] = []
    for dep_id in task.spec.depends_on:
        # Every dependency here is a chamber lookup, not a graph re-walk: ready_tasks (hivemind.
        # brood_chamber.task.graph) already required every one of them to be SUCCEEDED before this
        # task could ever be dispatched, so the status/outcome checks below are defensive, not
        # load-bearing, for a fresh dispatch -- they still matter for a retry or a resume, where a
        # dependency reported here is exactly the one this send re-confirms rather than re-decides.
        dep = await deps.chamber.get(dep_id)
        if dep.spec.role is not WorkerRole.SCOUT or dep.status is not TaskStatus.SUCCEEDED:
            continue  # Not a Scout, or not (yet) the outcome recon may carry.
        if dep.outcome is None or dep.outcome.scout_report is None:
            continue  # SUCCEEDED but no report: nothing to carry (should not happen for a Scout).
        dated.append((dep.updated_at, dep.outcome.scout_report))
    dated.sort(key=lambda pair: pair[0], reverse=True)  # Newest completion first.
    return tuple(report for _, report in dated[:MAX_RECON_REPORTS])


async def record_forage_granted(
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
    terms: AssignmentTerms,
    *,
    honey: tuple[HoneyHit, ...] = (),
) -> TaskAssign:
    """Build the TaskAssign a task's Warden receives, at `terms.attempt`, carrying `honey`."""
    reason = (
        "Resumed by the Queen's dispatcher (Clustering)."
        if terms.resume_from is not None
        else "Placed by the Queen's dispatcher."
    )
    return TaskAssign(
        task_id=task.id,
        goal_id=task.goal_id,
        cell_id=cell_id,
        role=task.spec.role,
        slot=ModelSlot.WORKER.to_wire(),
        objective=task.spec.objective,
        acceptance=task.spec.acceptance,
        leaves=task.spec.leaves,
        # Waggle 1.8 (roadmap step 10.3): the goal's own ceiling, so the Warden attenuates its
        # Worker's set to it and to the task's network needs below.
        capabilities=task.spec.capabilities,
        tempo=task.spec.needs.tempo.to_wire(),
        # Protocol 1.6: the Warden attaches exactly this Exoskeleton and grants exactly these
        # scopes; before it, both stopped at placement and never reached the Cell (ADR-0031).
        exoskeleton=task.spec.needs.exoskeleton_need(),
        network_scopes=task.spec.needs.network_scopes,
        # Roadmap step 6.10: the task's SUCCEEDED Scout dependencies, so a Forager's brief carries
        # what recon already found (_recon_for, filled in by send_grant_and_assign).
        recon=terms.recon,
        clearance=task.spec.clearance.to_wire(),
        grant_id=grant_id,
        attempt=terms.attempt,
        resume_from=terms.resume_from,
        honey=honey,
        reason=reason,
    )


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
