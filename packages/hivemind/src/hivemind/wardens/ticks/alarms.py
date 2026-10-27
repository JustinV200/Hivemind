"""Handle an Alarm's mapped WardenAction: RETRY, REBIND, ESCALATE or CANCEL_TASK.

Roadmap step 3.19's own dispatch map: "AlarmRaised(WORKER_CRASHED) or TaskResult(FAILED) from a
sub-bee -> policy: RETRY -> respawn the same binding with attempt+1 and resume_from = the sub-bee's
last HandoffRef... REBIND -> respawn on a stronger binding within the grant's allowed_bindings...
or ESCALATE when none is allowed; ESCALATE -> forward the AlarmRaised to the Queen with attempts...
CANCEL_TASK -> TaskResult(FAILED) to the Queen." Every one of the four closes the crashed sub-bee's
old link and retires its bookkeeping first (`retire_sub_bee`); RETRY and REBIND then respawn a fresh
sub-bee for the same task through `hivemind.wardens.spawn.spawn_sub_bee`, reusing the standing
grant. `send_alarm_to_queen` mints a brand-new Alarm (a Warden's own, such as `CELL_UNREACHABLE`,
never a sub-bee's forwarded one); forwarding a sub-bee's own Alarm keeps its `alarm_id` and
`origin` unchanged and only increments `attempts` (ADR-0012: "the same alarm_id travels unchanged
at every hop... so no level handles it twice"). Roadmap step 10.3 (ADR-0039): every rebind passes
the Guard's `slot_binding` point -- a Warden's own REBIND before the old sub-bee is retired (a
refused target escalates exactly like "no allowed binding left"), a Queen-sent one inside
`spawn_sub_bee` (a refusal reports the task FAILED with the reason, `report_refused`) -- and a
rebind that lands records `llm.rebound`, the declared-but-never-recorded kind. A respawn hands its
predecessor's slot straight to the fresh bee (`retire_sub_bee(keep_slot=True)`): giving it back to
the pool first let a parked assignment start beside the respawned bee, one over the cap.
`resume_from_handoff` is the same hand-over for a bee that stopped at a Handoff its own Warden
ordered (its context size, or its own Handoff, Rebind or Takeover lever): the fresh bee resumes
from that Handoff on the binding and attempt the order names (`SubBee.successor`), where before
nothing started it and the task stayed RUNNING with no bee. Every one of these respawns
resumes from a Handoff, so each passes the isolation resume gate first (`hivemind.wardens.
isolation.admit_resume`, ADR-0043), exactly as a Queen-sent `TaskAssign` does: before, a Warden's
own RETRY or REBIND was covered only because an isolation revokes the grant and a Worker refuses a
tainted Handoff itself; now a tainted resume is refused here, recorded, and the task held.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. These are `hivemind.wardens.warden.Warden`'s own delegates (see
    `hivemind.wardens.ticks.assign`'s own module docstring for why). Calls into `hivemind.cell`
    (HoneyClearance, CellIdentity), `hivemind.forage.slots` (ModelSlot), `hivemind.supervision`
    (Alarm, record_alarm_event -- this dispatch's own alarm-reaches-the-trail fix),
    `hivemind.wardens.spawn` (WardenCellContext, spawn_sub_bee, binding_check, authorize_binding),
    `hivemind.wardens.errors` (BindingRefusedError), `hivemind.wardens.isolation` (admit_resume),
    `hivemind.guard` (principals),
    `hivemind.pheromone` (LlmEvent) and waggle only.

Key invariants:
    - `retire_sub_bee` closes the old sub-bee's link and cancels its runtime task (if still running)
      exactly once per Alarm handled; a sub-bee is removed from `warden._sub_bees` before any
      respawn, so its old worker id is never briefly aliased to two runtime tasks.
    - `_rebind` never respawns on the sub-bee's own current binding: it always picks a different
      entry from the grant's `allowed`, or escalates when none is left.
    - Every RETRY/REBIND/CANCEL_TASK records `alarm.handled` and every path that reaches
      `_escalate` records `alarm.escalated`, exactly once per Alarm handled (this dispatch's own
      fix 1: an Alarm's own chain is now visible on the trail).
    - A rebind that lands records exactly one `llm.rebound`; a refused one records the Guard's
      `guard.denied` and never retires a sub-bee without either respawning it or reporting its
      task FAILED.
    - A respawn's fresh bee takes its predecessor's slot, never a second one: the slot is kept
      across the retire and released only if the Guard refuses the fresh bee's binding or the
      isolation gate refuses its Handoff.
    - No respawn spawns from a Handoff this Cell's store labels TAINTED (`admit_resume`).
    - `rebind_sub_bee` is the one place a fresh sub-bee is spawned on an explicit target binding
      without the grant's own `allowed`-bindings search: `hivemind.wardens.ticks.control`'s own
      Queen-driven REBIND path calls it with the binding key the Queen already resolved
      (`hivemind.queen.ticks.alarms._fallback_binding_key`), because `_next_allowed_binding` below
      can only ever name a *source* within the current grant (every v0 grant's own `allowed`
      entries share one `ModelSlot.WORKER`, so it never actually differs from `sub_bee.binding`) --
      this dispatch's own fix 3b/3c.
    - An Alarm from a bee the Warden asked for itself (`SubBee.local`, roadmap 11.9a) is kept on its
      row and handled by nothing here: no retry, no rebind, no escalation, because the Queen has
      neither the task nor a grant for it.

See Also:
    - docs/adr/0012-wardens-alarms-and-the-escalation-chain.md for the escalation chain this
      module implements the Warden's own hop of.
    - .claude/roadmap.md step 3.19's own dispatch map for the four actions this module handles.
    - hivemind.wardens.autopilot.table for decide, which maps a PolicyAction to one of these four.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import CellIdentity, HoneyClearance
from hivemind.common import reap
from hivemind.forage.slots import ModelSlot
from hivemind.guard import PrincipalRef, queen_principal, warden_principal
from hivemind.pheromone import LlmEvent
from hivemind.supervision import Alarm, record_alarm_event
from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.errors import BindingRefusedError
from hivemind.wardens.isolation import admit_resume
from hivemind.wardens.links import send_guarded
from hivemind.wardens.spawn import (
    BindingTarget,
    Routed,
    WardenCellContext,
    authorize_binding,
    binding_check,
    route_rebind,
    spawn_sub_bee,
    stop_sub_bee,
)
from hivemind.wardens.spawn.sub_bee import SuccessorOrder
from hivemind.wardens.ticks.tools import comb_seams
from hivemind.wardens.ticks.trail_ship import ship_trail_before_result
from waggle.envelope import wrap
from waggle.ids import TaskId, new_alarm_id, new_event_id
from waggle.messages import AlarmSeverity
from waggle.messages.base import MAX_REASON_CHARS as WIRE_REASON_CHARS
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import TaskAssign, TaskOutcome, TaskResult

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden
    from waggle.messages.forage import GrantIssued

MAX_REASON_CHARS = 2_000  # Mirrors waggle.messages.task.reports.MAX_SUMMARY_CHARS's own bound.

__all__ = [
    "MAX_REASON_CHARS",
    "cell_context",
    "handle_alarm_action",
    "rebind_sub_bee",
    "record_handled",
    "report_refused",
    "resume_from_handoff",
    "retire_sub_bee",
    "send_alarm_to_queen",
]


async def handle_alarm_action(
    warden: Warden,
    sub_bee: SubBee,
    alarm: AlarmRaised,
    action: WardenAction,
    binding: str | None,
) -> None:
    """Carry out RETRY, REBIND, ESCALATE or CANCEL_TASK for `alarm`, concerning `sub_bee`.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        sub_bee: The sub-bee `alarm` concerns.
        alarm: The AlarmRaised being handled.
        action: The WardenAction decided for it.
        binding: An explicit target `[llm.slots]` key for REBIND (from an awake decision); None
            lets `_rebind` pick the next entry in the grant's own allowed bindings.
    """
    if sub_bee.local is not None:
        # The Warden's own run (roadmap 11.9a): an Alarm ends it and is the Warden's to read. It is
        # never retried, rebound or sent to the Queen, who has no such task and no grant for it.
        sub_bee.local.alarm = alarm
        return
    if action is WardenAction.RETRY:
        await _record_handled(warden, alarm, action)
        await _respawn(warden, sub_bee, alarm, binding_override=None)
    elif action is WardenAction.REBIND:
        await _rebind(warden, sub_bee, alarm, binding)
    elif action is WardenAction.ESCALATE:
        await _escalate(warden, sub_bee, alarm)
    elif action is WardenAction.CANCEL_TASK:
        await _record_handled(warden, alarm, action)
        await _cancel_task(warden, sub_bee, alarm)


async def _respawn(
    warden: Warden, sub_bee: SubBee, alarm: AlarmRaised, *, binding_override: BindingTarget | None
) -> SubBee | None:
    """Retire `sub_bee` and start a fresh one for the same task, attempt+1, from its last Handoff.

    Escalates instead when the grant this task ran under, or this Warden's own Cell/lease/
    session, is no longer available to respawn onto; returns the fresh sub-bee, or None.
    """
    grant = warden._grants.get(sub_bee.assignment.grant_id)
    ctx = cell_context(warden)
    if grant is None or ctx is None:
        await _escalate(warden, sub_bee, alarm)
        return None
    return await _retire_and_spawn(warden, sub_bee, ctx, binding_override, None)


async def _rebind(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised, binding: str | None) -> None:
    """Respawn on `binding`, or on a stronger routed binding; escalate when none is left.

    Roadmap step 10.3: the target passes the `slot_binding` point first, ordered by this Warden;
    a refused target escalates exactly like no target at all, and nothing is retired. Roadmap
    step 8.1: with no explicit target, routing runs again with the grade floor raised by the
    bee's attempt count, and the grant's next allowed binding is used only when routing is not
    wired or finds nothing stronger than the binding that just failed.
    """
    grant = warden._grants.get(sub_bee.assignment.grant_id)
    ctx = cell_context(warden)
    if grant is None or ctx is None:
        await _escalate(warden, sub_bee, alarm)
        return
    target = binding or _stronger_binding(ctx, sub_bee, grant)
    key = target.key if isinstance(target, Routed) else target
    if key is None:
        await _escalate(warden, sub_bee, alarm)
        return
    check = binding_check(ctx, sub_bee.assignment, grant, key, sub_bee.capabilities)
    if not (await authorize_binding(warden._deps, check)).allowed:
        await _escalate(warden, sub_bee, alarm)
        return
    await _record_handled(warden, alarm, WardenAction.REBIND)
    fresh = await _respawn(warden, sub_bee, alarm, binding_override=target)
    if fresh is not None:
        await _record_rebound(warden, sub_bee, fresh, warden_principal(warden._warden_id))


def _stronger_binding(
    ctx: WardenCellContext, sub_bee: SubBee, grant: GrantIssued
) -> Routed | str | None:
    """Route `sub_bee`'s task again at a raised floor; else the grant's next allowed binding."""
    scope = ctx.route_scope(sub_bee.assignment, grant)
    return route_rebind(scope, sub_bee.binding) or _next_allowed_binding(sub_bee, grant)


def _next_allowed_binding(sub_bee: SubBee, grant: GrantIssued) -> str | None:
    """Return the first allowed-binding manifest key that differs from `sub_bee`'s own."""
    for allowed in grant.allowed:
        key = ModelSlot.from_wire(allowed.slot).manifest_key
        if key != sub_bee.binding:
            return key
    return None


async def rebind_sub_bee(warden: Warden, sub_bee: SubBee, target_binding: str) -> None:
    """Retire `sub_bee` and respawn it on `target_binding`, one attempt higher, from its Handoff.

    The counterpart `hivemind.wardens.ticks.control`'s own Queen-driven REBIND path calls once the
    Queen has already named an explicit target key (module docstring's fix 3b/3c): unlike `_rebind`
    above, this never searches the grant's own `allowed` bindings -- the caller already resolved
    one -- so it is the one place both the sub-bee-driven and the Queen-driven rebind paths share.

    Roadmap step 10.3: the Queen ordered this binding, so she is the principal at the
    `slot_binding` point inside `spawn_sub_bee`; before this step a Queen-sent REBIND was never
    checked against the grant at all. A refused binding reports the task FAILED with the reason.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        sub_bee: The sub-bee to respawn.
        target_binding: The `[llm.slots]` manifest key to respawn on.
    """
    grant = warden._grants.get(sub_bee.assignment.grant_id)
    ctx = cell_context(warden)
    if grant is None or ctx is None:
        return  # Defensive: nothing to respawn onto; the next liveness sweep notices the gap.
    queen = queen_principal(warden._deps.identity.hive_id)
    fresh = await _retire_and_spawn(warden, sub_bee, ctx, target_binding, queen)
    if fresh is not None:
        await _record_rebound(warden, sub_bee, fresh, queen)


async def report_refused(warden: Warden, assignment: TaskAssign, reason: str) -> None:
    """Report `assignment`'s task FAILED to the Queen because the Guard refused what it needed.

    A refused binding (roadmap step 10.3) cannot run the task on this Warden at all; reporting it
    FAILED with the Guard's own reason lets the Queen retry, fail or escalate it, rather than
    leaving it RUNNING with no sub-bee.

    Args:
        warden: The owning Warden.
        assignment: The task's TaskAssign.
        reason: The Guard's reason sentence (already `guard.denied` on the trail).
    """
    result = TaskResult(
        task_id=assignment.task_id,
        attempt=assignment.attempt,
        outcome=TaskOutcome.FAILED,
        summary=reason[:MAX_REASON_CHARS],
        clearance=assignment.clearance,
        artifacts=(),
        checked_by=warden._warden_id,
        handoff=assignment.resume_from,
        spend=0.0,
        reason=reason[:WIRE_REASON_CHARS],
    )
    # The same ordering _send_result keeps: this Cell's trail rows (the refusal) ship first.
    await ship_trail_before_result(warden)
    message = wrap(result, warden._deps.hop, clock=warden._deps.clock)
    await send_guarded(warden._deps.queen_link, message)  # A gone link is logged, not raised.


def cell_context(warden: Warden) -> WardenCellContext | None:
    """Return this Warden's own Cell context for a respawn, or None when it holds no lease."""
    if warden._cell is None or warden._lease is None or warden._session is None:
        return None
    return WardenCellContext(
        warden_id=warden._warden_id,
        deps=warden._deps,
        ceiling=warden._ceiling,
        cell=warden._cell,
        lease=warden._lease,
        session=warden._session,
        hosting=warden._hosting_plan,
        comb=comb_seams(warden),  # Roadmap 9.7: a respawned bee is given the same Comb tools.
    )


async def resume_from_handoff(warden: Warden, sub_bee: SubBee) -> None:
    """Retire a bee stopped at its own Warden's Handoff, and start the fresh bee resuming it.

    Each lever's own meaning (`hivemind.supervision.intervention`): a Handoff's fresh bee carries
    the same attempt on the same binding; a Rebind's binds the lever's slot and a Takeover's the
    Warden's own, each as attempt+1 -- all in the slot the stopped bee kept, from the Handoff its
    last checkpoint reported (`SubBee.successor` names which). A grant or lease the Queen has
    taken back (an isolation revokes the grant before it pauses anything) leaves the task to her,
    so the bee is retired and nothing starts; a binding the Guard refuses reports the task FAILED.

    Args:
        warden: The owning Warden (read and written directly; see the module docstring).
        sub_bee: The stopped bee (`SubBee.awaits_successor`).
    """
    order = sub_bee.successor or SuccessorOrder()
    grant = warden._grants.get(sub_bee.assignment.grant_id)
    ctx = cell_context(warden)
    if grant is None or ctx is None:
        # The Queen took the grant or the lease back: what happens to the task next is hers.
        await retire_sub_bee(warden, sub_bee)
        return
    # A Handoff is the work carrying on (the same attempt); a Rebind or Takeover recovers it.
    attempt = sub_bee.attempt + 1 if order.new_attempt else sub_bee.attempt
    successor = sub_bee.assignment.model_copy(
        update={"attempt": attempt, "resume_from": sub_bee.last_handoff}
    )
    await retire_sub_bee(warden, sub_bee, keep_slot=True)
    fresh = await _spawn_into_slot(warden, ctx, successor, order.binding or sub_bee.binding, None)
    if fresh is not None and fresh.binding != sub_bee.binding:
        # A rebind that landed, the one fact `llm.rebound` exists to record.
        await _record_rebound(warden, sub_bee, fresh, warden_principal(warden._warden_id))


async def _retire_and_spawn(
    warden: Warden,
    sub_bee: SubBee,
    ctx: WardenCellContext,
    binding: BindingTarget | None,
    orderer: PrincipalRef | None,
) -> SubBee | None:
    """Retire `sub_bee` and spawn its successor at attempt+1, from its Handoff, in its slot."""
    await retire_sub_bee(warden, sub_bee, keep_slot=True)
    new_assignment = sub_bee.assignment.model_copy(
        update={"attempt": sub_bee.attempt + 1, "resume_from": sub_bee.last_handoff}
    )
    return await _spawn_into_slot(warden, ctx, new_assignment, binding, orderer)


async def _spawn_into_slot(
    warden: Warden,
    ctx: WardenCellContext,
    assignment: TaskAssign,
    binding: BindingTarget | None,
    orderer: PrincipalRef | None,
) -> SubBee | None:
    """Spawn `assignment` into the slot its retired predecessor kept, or report it refused.

    Every Warden-side respawn comes through here (RETRY, REBIND, a Queen-ordered rebind and a
    context-size Handoff's successor), so the isolation resume gate stands in front of all of
    them exactly as it stands in front of every `TaskAssign`.
    """
    grant = warden._grants[assignment.grant_id]  # Every caller checked it is there.
    # ADR-0043: a respawn resumes from its predecessor's Handoff just as a Queen-sent resume does,
    # so it passes the same isolation gate. A tainted one is refused there (grant withdrawn, the
    # Queen told the task is held), and the kept slot goes back to the pool.
    if not await admit_resume(warden, assignment):
        warden._sub_bee_slots.release()
        return None
    try:
        fresh = await spawn_sub_bee(ctx, assignment, grant, binding, orderer)
    except BindingRefusedError as refused:
        # Nothing took the kept slot: it goes back to the pool, and the task to the Queen.
        warden._sub_bee_slots.release()
        await report_refused(warden, assignment, refused.reason)
        return None
    warden._sub_bees[fresh.worker_id] = fresh
    warden._sub_bee_iters[fresh.worker_id] = fresh.link.receive()
    return fresh


async def _record_rebound(
    warden: Warden, old: SubBee, fresh: SubBee, ordered_by: PrincipalRef
) -> None:
    """Record `llm.rebound`: `old`'s task now runs on `fresh`'s binding, inside its grant."""
    deps = warden._deps
    event = LlmEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind="llm.rebound",
        subject_id=fresh.worker_id,
        slot=fresh.assignment.slot,
        payload={
            "task_id": fresh.task_id,
            "from_binding": old.binding,
            "to_binding": fresh.binding,
            "ordered_by": ordered_by.kind.value,
        },
    )
    await deps.trail.record(event)


async def _cancel_task(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised) -> None:
    """Retire `sub_bee` and report its task FAILED to the Queen."""
    await retire_sub_bee(warden, sub_bee)
    reason = f"Cancelled after {alarm.kind.value}: {alarm.detail}"[:MAX_REASON_CHARS]
    await _send_result(warden, sub_bee, outcome=TaskOutcome.FAILED, reason=reason)


async def _escalate(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised) -> None:
    """Forward `alarm` to the Queen unchanged apart from an incremented attempt count."""
    del sub_bee  # Not needed to forward: the Alarm already carries every reference it needs.
    await record_alarm_event(
        warden._deps.trail,
        _identity(warden),
        warden._deps.clock,
        Alarm.from_wire(alarm),
        "alarm.escalated",
    )
    forwarded = alarm.model_copy(update={"attempts": alarm.attempts + 1})
    await _send_to_queen(warden, forwarded)


async def _record_handled(warden: Warden, alarm: AlarmRaised, action: WardenAction) -> None:
    """Record `alarm.handled` for a RETRY/REBIND/CANCEL_TASK this Warden resolved, no escalation."""
    await record_handled(warden, alarm, action.value)


async def record_handled(warden: Warden, alarm: AlarmRaised, action: str) -> None:
    """Record `alarm.handled` for an Alarm this Warden resolved itself, naming what it did.

    Args:
        warden: The owning Warden, whose trail and identity record it.
        alarm: The Alarm that was handled.
        action: What the Warden did about it: a `WardenAction`'s value, or a word of its own such
            as `hivemind.wardens.ticks.lease.HELD_FOR_CELL`.
    """
    await record_alarm_event(
        warden._deps.trail,
        _identity(warden),
        warden._deps.clock,
        Alarm.from_wire(alarm),
        "alarm.handled",
        action=action,
    )


def _identity(warden: Warden) -> CellIdentity:
    """Build the CellIdentity every alarm.* event this module records is stamped with."""
    identity = warden._deps.identity
    return CellIdentity(hive_id=identity.hive_id, node_id=identity.node_id, actor=identity.actor)


async def send_alarm_to_queen(
    warden: Warden, *, kind: AlarmKind, detail: str, reason: str, task_id: TaskId | None = None
) -> None:
    """Mint and send a brand-new Alarm this Warden itself raises (never a forwarded one).

    Args:
        warden: The owning Warden.
        kind: What went wrong.
        detail: The failing assertion or observation.
        reason: Why the Warden escalates instead of handling it itself.
        task_id: The task concerned, if any.
    """
    alarm = AlarmRaised(
        alarm_id=new_alarm_id(warden._deps.clock),
        kind=kind,
        severity=AlarmSeverity.WARNING,
        origin=warden._warden_id,
        attempts=0,
        raised_at=warden._deps.clock.now(),
        context=AlarmContext(
            task_id=task_id, cell_id=None, worker_id=None, event_id=None, handoff=None
        ),
        detail=detail,
        clearance=HoneyClearance.C1.to_wire(),
        reason=reason,
    )
    # This Warden raises it, so this Warden opens its trail chain (the same rule heartbeat.py
    # follows for WORKER_STALLED): the Queen's later alarm.escalated needs a raised to hang off.
    await record_alarm_event(
        warden._deps.trail,
        _identity(warden),
        warden._deps.clock,
        Alarm.from_wire(alarm),
        "alarm.raised",
    )
    await _send_to_queen(warden, alarm)


async def retire_sub_bee(warden: Warden, sub_bee: SubBee, *, keep_slot: bool = False) -> None:
    """Stop `sub_bee`, reap what waited on its link, close the link, drop it, free its slot.

    The one way a sub-bee leaves its Warden, whatever ended it: a claim accepted (`results`), an
    Alarm's action (here), a quarantine (`hivemind.wardens.quarantine`), a Heartbeat saying it has
    ended (`heartbeat.record_heartbeat`), a cancel of an ended bee or the lease taken back
    (`control`), and `Warden.stop`; so its slot always comes back for the next assignment.

    The order is the whole point (codingrules section 11). A bare `runtime_task.cancel()` with no
    await leaves that task destroyed while still pending, and closing the link while this Warden's
    own receive task is still suspended inside the link's `receive()` generator closes that
    generator while it is running. So: stop the runtime cooperatively and reap it (`stop_sub_bee`),
    reap the receive task that was awaiting this link, and only then close the link.

    Args:
        warden: The owning Warden, whose sub-bee tables this drops the entry from.
        sub_bee: The sub-bee to retire.
        keep_slot: True when a fresh bee takes this one's slot straight away (a respawn, a
            rebind, a handoff's successor): the slot passes to it instead of back to the pool,
            where a parked assignment could otherwise take it first.
    """
    await stop_sub_bee(sub_bee, warden._deps.clock)
    warden._sub_bees.pop(sub_bee.worker_id, None)
    warden._sub_bee_iters.pop(sub_bee.worker_id, None)
    receive_task = warden._receive_tasks.pop(sub_bee.worker_id, None)
    if receive_task is not None:
        await reap(receive_task)
    if not keep_slot and sub_bee.local is None:
        # Back to the pool for the next assignment, unless a successor is about to take it. A bee
        # the Warden started for itself (roadmap 11.9a) never took one: the Queen's grant is not
        # what it runs under.
        warden._sub_bee_slots.release()
    await sub_bee.link.close()


async def _send_to_queen(warden: Warden, alarm: AlarmRaised) -> None:
    """Wrap and send `alarm` to the Queen over this Warden's own queen link.

    Codingrules 8.8: Alarms queue while a Warden is disconnected. A Warden that dials the Queen
    holds a reconnecting link (`hivemind.wardens.offline`) that queues this one durably and sends
    it, in order, once the link is back. On a link that is closed for good the Queen never learns
    of it from this hop -- `alarm.raised`/`alarm.handled` above are already durable on this
    Warden's own trail segment regardless.
    """
    envelope = wrap(alarm, warden._deps.hop, clock=warden._deps.clock)
    await send_guarded(warden._deps.queen_link, envelope)


async def _send_result(
    warden: Warden, sub_bee: SubBee, *, outcome: TaskOutcome, reason: str
) -> None:
    """Build and send this Warden's own verified TaskResult to the Queen."""
    result = TaskResult(
        task_id=sub_bee.task_id,
        attempt=sub_bee.attempt,
        outcome=outcome,
        summary=reason[:MAX_REASON_CHARS],
        clearance=sub_bee.assignment.clearance,
        artifacts=(),
        checked_by=warden._warden_id,
        handoff=sub_bee.last_handoff,
        spend=0.0,
        reason=reason[:MAX_REASON_CHARS],
    )
    # The Queen tears this Cell down the moment a FAILED result lands: ship the task's own trail
    # rows first, over the same ordered link (hivemind.wardens.ticks.trail_ship).
    await ship_trail_before_result(warden)
    # Codingrules 8.8: a result queues while disconnected, which a reconnecting link does
    # (same as _send_to_queen above) -- this sub-bee is already retired either way.
    envelope = wrap(result, warden._deps.hop, clock=warden._deps.clock)
    await send_guarded(warden._deps.queen_link, envelope)
