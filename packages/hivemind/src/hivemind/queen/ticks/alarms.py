"""Define handle_alarm: what REBIND, QUARANTINE_BEE, ISOLATE_CELL, RETRY_TASK and the rest do.

Roadmap step 3.20's own dispatch map: "escalated AlarmRaised -> policy + attempts: RETRY_TASK
(re-dispatch, attempt+1), REBIND (Intervene(REBIND, task_id, slot=<the fallback binding key from
deps.bindings for the task's slot chain>) to the Warden... when no fallback exists ->
ESCALATE_TO_HUMAN), FAIL_TASK (chamber.fail), ESCALATE_TO_HUMAN (human_inbox.add_alarm and
alarm.escalated is the supervision owner's event: record what you own only, i.e. queen.decided)."
`RETRY_TASK` and `FAIL_TASK` reuse `hivemind.queen.ticks.results.retry_task`/`.fail_task`, the same
two functions a `TaskResult(FAILED)` drives, since "retry this task" means the same chamber calls
regardless of which report triggered the decision. `REBIND`'s own wire message is built through
`hivemind.supervision.intervention.Rebind`/`to_wire`, the same `Intervention` lever machinery
`Queen.intervene` (the `Supervisor` protocol method) already uses -- not a hand-rolled `Intervene`
-- because the wire `Intervene.slot` field only ever carries a `ModelSlot`'s own UPPER_SNAKE wire
value (`waggle.messages.base.SLOT_PATTERN`), never a lowercase `[llm.slots]` named-binding key such
as `"local_worker"`; the resolved fallback *key* is recorded on the trail instead (this dispatch's
own report flags the gap this works around, since neither `wardens/**` nor `waggle/**` is this
dispatch's to touch). `QUARANTINE_BEE` (roadmap step 10.6c, a `PolicyAction.QUARANTINE` row) reads
the lever off the Alarm (its task, its bee and its trail event) and sends the Warden that holds the
task `Intervene(QUARANTINE)` through `hivemind.queen.quarantine`; an Alarm that cannot scope one
reaches the human instead. `ISOLATE_CELL` (roadmap step 10.6a, a `PolicyAction.ISOLATE` row, the
Queen's alone) records her decision and isolates the Alarm's Cell through the one isolation path
(`hivemind.queen.isolation`); an Alarm naming no Cell she can reach, or the Hive Stand, reaches the
human. `FAIL_TASK` also sends the Warden that raised the Alarm a `TaskCancel` for the task: that
Warden may still run its bee (an Alarm about a bee over its quota rather than crashed) or hold its
escalated FAILED row waiting on her, and only a cancel ends either, so without it the chamber read
FAILED while a bee kept working on the task.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick dispatch, once per decided
    `REBIND`/`QUARANTINE_BEE`/`ESCALATE_TO_HUMAN`/`RETRY_TASK`/`FAIL_TASK` for an `AlarmRaised`.
    Calls into
    `hivemind.cell` (CellIdentity), `hivemind.common.logging`, `hivemind.forage.slots`
    (ModelSlot), `hivemind.queen.autopilot` (QueenAction), `hivemind.queen.chat` (post_alarm,
    roadmap step 10.5: an escalated Alarm is appended to the chat; CANCEL_SEND_TIMEOUT_S, the
    bound on FAIL_TASK's cancel), `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.
    human_inbox` (HumanInbox), `hivemind.queen.isolation` (isolate_cell, roadmap step 10.6a),
    `hivemind.queen.quarantine` (lever_from_alarm, order_quarantine),
    `hivemind.queen.ticks.results` (fail_task, retry_task),
    `hivemind.queen.trail` (record_event), `hivemind.supervision` (Alarm, record_alarm_event,
    intervention.Rebind, to_wire) and waggle only.

Key invariants:
    - `handle_alarm` never re-decides `action`: it is a pure dispatch over whatever
      `hivemind.queen.autopilot.table.decide` (or an awake `QueenDecision`) already chose.
    - A REBIND with no fallback binding for `ModelSlot.WORKER`, or a `context.task_id` of `None`,
      always falls back to `ESCALATE_TO_HUMAN` rather than sending a message that names nothing to
      act on.
    - Every REBIND/RETRY_TASK/FAIL_TASK records `alarm.handled` and `_escalate` records
      `alarm.escalated` (this dispatch's own fix 1). A REBIND or RETRY_TASK also remembers its own
      Alarm on `handling.pending_alarms`, keyed by task id, so `hivemind.queen.queen`'s own
      COMPLETE_TASK handling can record `alarm.resolved` once the rebound or retried attempt
      actually succeeds (fix 3d) -- the one Alarm outcome this module itself never reaches, since
      it always runs before the task's own next result is even in flight.
    - A task FAILED here is always followed by a TaskCancel to its Warden, bounded by
      `CANCEL_SEND_TIMEOUT_S`; a link that cannot take it is logged, never a reason to raise.
    - The `Intervene(REBIND)` this module sends always fills `binding` with the same fallback key
      it resolved for itself (fix 3c): the receiving Warden (`hivemind.wardens.ticks.control`) has
      no other way to learn which `[llm.slots]` key to respawn on.

See Also:
    - .claude/roadmap.md step 3.20's own dispatch map for the exact Alarm handling this module
      implements.
    - .claude/roadmap.md step 3.22 scenario (c) for the rebind-then-completes e2e this handles.
    - hivemind.queen.ticks.results for retry_task and fail_task, reused by RETRY_TASK/FAIL_TASK.
    - hivemind.supervision.intervention for Rebind and to_wire, the levers REBIND is built from.
"""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass

from hivemind.cell import CellIdentity
from hivemind.common.logging import get_logger
from hivemind.forage.slots import ModelSlot
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.chat import CANCEL_SEND_TIMEOUT_S, post_alarm
from hivemind.queen.cluster.triggers import cluster_if_down
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.errors import UnknownCellError
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.isolation import IsolationOrder, IsolationSite, Isolator, isolate_cell
from hivemind.queen.quarantine import lever_from_alarm, order_quarantine
from hivemind.queen.ticks.results import fail_task, retry_task
from hivemind.queen.trail import record_event
from hivemind.supervision import Alarm, record_alarm_event
from hivemind.supervision.intervention import Rebind, to_wire
from waggle.envelope import wrap
from waggle.errors import TransportError
from waggle.ids import TaskId, WardenId
from waggle.messages.base import MAX_REASON_CHARS as WIRE_REASON_CHARS
from waggle.messages.supervision import AlarmKind, AlarmRaised, Intervene
from waggle.messages.task import TaskCancel

MAX_ALARM_REASON_CHARS = 2_000  # Matches waggle.messages.supervision.alarms.MAX_DETAIL_CHARS.

_LOG = get_logger(__name__)

__all__ = ["MAX_ALARM_REASON_CHARS", "AlarmHandling", "handle_alarm"]


@dataclass(frozen=True, slots=True)
class AlarmHandling:
    """What `handle_alarm` needs beyond `deps`/`wardens`, grouped to stay within codingrules 5.1.

    Attributes:
        human_inbox: Where ESCALATE_TO_HUMAN lands.
        warden_id: The Warden that forwarded `payload` (the envelope's own sender).
        payload: The escalated AlarmRaised.
        action: The already-decided QueenAction; one of the four `handle_alarm` dispatches.
        attempts: The Queen's own shared attempt counter (module docstring: the chamber's
            `Task.attempt` cannot track a RUNNING task's own retries, so the Queen tracks it
            instead).
        pending_alarms: The Queen's own task_id -> Alarm table for a REBIND or RETRY_TASK still
            awaiting its own outcome; `hivemind.queen.queen`'s own COMPLETE_TASK handling pops
            from it to record `alarm.resolved` once the retried or rebound attempt succeeds
            (fix 3d).
    """

    human_inbox: HumanInbox
    warden_id: WardenId
    payload: AlarmRaised
    action: QueenAction
    attempts: MutableMapping[TaskId, int]
    pending_alarms: MutableMapping[TaskId, Alarm]


async def handle_alarm(
    deps: QueenDeps, wardens: Sequence[WardenLink], handling: AlarmHandling
) -> None:
    """Carry out RECORD, REBIND, ESCALATE_TO_HUMAN, RETRY_TASK or FAIL_TASK for an Alarm.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        handling: The human inbox, the forwarding Warden, the Alarm, the decided action and the
            Queen's own shared attempt counter (module docstring: the chamber's `Task.attempt`
            cannot track a RUNNING task's own retries, so the Queen tracks it instead).
    """
    payload, action = handling.payload, handling.action
    task_id = payload.context.task_id
    if action is QueenAction.RECORD:
        # Noted on the trail, never acted on: an Alarm whose paired TaskResult carries the
        # decision (autopilot.table's own ACCEPTANCE_FAILED rule), or one about a task already
        # terminal. Before this branch either fell through to `_escalate` below.
        alarm = Alarm.from_wire(payload)
        await record_alarm_event(
            deps.trail, _identity(deps), deps.clock, alarm, "alarm.handled", action="RECORD"
        )
        return
    if action is QueenAction.RETRY_TASK and task_id is not None:
        await _record_handled(deps, handling, task_id, "RETRY_TASK")
        next_attempt = handling.attempts.get(task_id, 1) + 1
        handling.attempts[task_id] = next_attempt
        await retry_task(deps, wardens, task_id, next_attempt)
    elif action is QueenAction.FAIL_TASK and task_id is not None:
        await _fail(deps, wardens, handling, task_id)
    elif action is QueenAction.REBIND:
        await _rebind(deps, wardens, handling)
    elif action is QueenAction.QUARANTINE_BEE:
        await _quarantine(deps, wardens, handling)
    elif action is QueenAction.ISOLATE_CELL:
        await _isolate(deps, wardens, handling)
    else:
        # ESCALATE_TO_HUMAN, or RETRY_TASK/FAIL_TASK for an Alarm naming no task: escalate rather
        # than silently dropping an Alarm this table decided needs a human.
        await _escalate(deps, handling.human_inbox, payload)


async def _fail(
    deps: QueenDeps, wardens: Sequence[WardenLink], handling: AlarmHandling, task_id: TaskId
) -> None:
    """Fail the Alarm's task for good, then tell the Warden that raised it to cancel the task."""
    payload = handling.payload
    alarm = Alarm.from_wire(payload)
    await record_alarm_event(
        deps.trail, _identity(deps), deps.clock, alarm, "alarm.handled", action="FAIL_TASK"
    )
    handling.pending_alarms.pop(task_id, None)  # A failed task never resolves its own Alarm.
    await fail_task(deps, task_id, _reason(payload))
    # A FAILED task keeps no bee: whatever its Warden still runs or holds for it ends here.
    link = next((w for w in wardens if w.warden_id == handling.warden_id), None)
    if link is not None:
        await _cancel_on(deps, link, task_id, _reason(payload))


async def _cancel_on(deps: QueenDeps, link: WardenLink, task_id: TaskId, reason: str) -> None:
    """Send `link`'s Warden a TaskCancel for `task_id`, bounded; a stuck link is only logged."""
    order = TaskCancel(task_id=task_id, grace_s=0.0, reason=reason[:WIRE_REASON_CHARS])
    try:
        # External wait: one frame onto the Warden's link, milliseconds; bounded all the same.
        async with asyncio.timeout(CANCEL_SEND_TIMEOUT_S):
            await link.transport.send(wrap(order, link.hop, clock=deps.clock))
    except (TimeoutError, TransportError) as error:
        # The task is already FAILED; a Warden this cannot reach is judged by its own liveness.
        _LOG.warning("queen.fail_task.cancel_unsent", task_id=task_id, error=str(error))


async def _rebind(deps: QueenDeps, wardens: Sequence[WardenLink], handling: AlarmHandling) -> None:
    """Send Intervene(REBIND) to the forwarding Warden, or ESCALATE_TO_HUMAN with no fallback."""
    payload = handling.payload
    task_id = payload.context.task_id
    link = next((w for w in wardens if w.warden_id == handling.warden_id), None)
    fallback_key = _fallback_binding_key(deps)
    if task_id is None or link is None or fallback_key is None:
        if task_id is not None and payload.kind is AlarmKind.PROVIDER_UNAVAILABLE:
            await _cluster_or_retry(deps, wardens, handling, task_id)
            return
        await _escalate(deps, handling.human_inbox, payload)
        return
    intervention = Rebind(reason=_reason(payload), slot=ModelSlot.WORKER)
    intervention_action, slot = to_wire(intervention)
    message = Intervene(
        action=intervention_action,
        subject=None,
        task_id=task_id,
        slot=slot,
        # The one field a Warden cannot derive on its own (module docstring's fix 3c): the
        # fallback chain (`[llm.slots] fallback`) is a Queen-side concept the Warden's own grant
        # never carries, so the Queen ships the already-resolved manifest key on the wire.
        binding=fallback_key,
        alarm_id=payload.alarm_id,
        reason=intervention.reason,
    )
    # A Warden that never receives this REBIND is, by definition, one this tick cannot reach
    # anyway: the same task sits RUNNING either way, and liveness (hivemind.queen.ticks.liveness)
    # is what eventually raises a human-visible Alarm for a Warden that stays unreachable.
    await link.send(wrap(message, link.hop, clock=deps.clock))
    await record_event(deps, "queen.decided", task_id, action="REBIND", binding=fallback_key)
    await _record_handled(deps, handling, task_id, "REBIND")


async def _quarantine(
    deps: QueenDeps, wardens: Sequence[WardenLink], handling: AlarmHandling
) -> None:
    """Order the Warden of the Alarm's task to quarantine the bee it names, or escalate.

    Roadmap step 10.6c: the order is the Queen's; the quarantine itself is the Warden's one code
    path. An Alarm that names no task, no bee's event or no placed Warden cannot scope one, so it
    reaches the human instead, exactly like any other decision this table cannot carry out.
    """
    payload = handling.payload
    lever = lever_from_alarm(payload)
    sent = lever is not None and await order_quarantine(deps, wardens, lever, payload.alarm_id)
    if not sent:
        await _escalate(deps, handling.human_inbox, payload)
        return
    alarm = Alarm.from_wire(payload)
    await record_alarm_event(
        deps.trail, _identity(deps), deps.clock, alarm, "alarm.handled", action="QUARANTINE_BEE"
    )


async def _isolate(deps: QueenDeps, wardens: Sequence[WardenLink], handling: AlarmHandling) -> None:
    """Isolate the Alarm's Cell through the one isolation path, or put the Alarm to the human.

    Roadmap step 10.6a: an ISOLATE row of the Queen's own policy (a Warden's never loads one).
    Her decision is on the trail first; an Alarm naming no Cell, a Cell with no attached Warden,
    or the Hive Stand (isolated only by the human) reaches the human instead, like any decision
    this table cannot carry out.
    """
    payload = handling.payload
    cell_id = payload.context.cell_id
    if cell_id is None:
        await _escalate(deps, handling.human_inbox, payload)
        return
    await record_event(
        deps, "queen.decided", cell_id, action="ISOLATE_CELL", alarm_id=payload.alarm_id
    )
    order = IsolationOrder(
        cell_id=cell_id,
        ordered_by=Isolator.QUEEN,
        reason=f"Alarm {payload.alarm_id} ({payload.kind.value}) under an ISOLATE policy row.",
        evidence=(payload.context.event_id,) if payload.context.event_id else (),
    )
    site = IsolationSite(deps=deps, wardens=wardens, human_inbox=handling.human_inbox)
    try:
        outcome = await isolate_cell(site, order)
    except UnknownCellError:
        outcome = None  # Its Warden is gone: nothing here can reach the Cell.
    if outcome is None or outcome.refusal is not None:
        await _escalate(deps, handling.human_inbox, payload)
        return
    alarm = Alarm.from_wire(payload)
    await record_alarm_event(
        deps.trail, _identity(deps), deps.clock, alarm, "alarm.handled", action="ISOLATE_CELL"
    )


async def _cluster_or_retry(
    deps: QueenDeps, wardens: Sequence[WardenLink], handling: AlarmHandling, task_id: TaskId
) -> None:
    """Handle a provider outage with no fallback binding: cluster if it is down, else retry.

    A dead provider with nowhere to rebind to is exactly what Clustering exists for
    (codingrules section 8.13), so it never reaches the human: the Queen probes the task's own
    providers now (`cluster_if_down`) and, if one is down, the pause *is* the handling; the
    Alarm stays pending and resolves when the resumed task succeeds. A provider that answers
    was a blip, and the task is simply dispatched again. Either way the Queen's own
    `alarm_attempt_limit` ceiling still escalates a task that keeps failing.
    """
    if await cluster_if_down(deps, deps.cluster_state, wardens, task_id):
        await record_event(deps, "queen.decided", task_id, action="CLUSTER")
        await _record_handled(deps, handling, task_id, "CLUSTER")
        return
    await _record_handled(deps, handling, task_id, "RETRY_TASK")
    next_attempt = handling.attempts.get(task_id, 1) + 1
    handling.attempts[task_id] = next_attempt
    await retry_task(deps, wardens, task_id, next_attempt)


async def _escalate(deps: QueenDeps, human_inbox: HumanInbox, payload: AlarmRaised) -> None:
    """Add `payload` to the human inbox and the chat, and record the decision to escalate."""
    alarm = Alarm.from_wire(payload)
    human_inbox.add_alarm(alarm)
    subject = payload.context.task_id or deps.identity.hive_id
    await record_event(
        deps, "queen.decided", subject, action="ESCALATE_TO_HUMAN", alarm_id=alarm.id
    )
    await record_alarm_event(deps.trail, _identity(deps), deps.clock, alarm, "alarm.escalated")
    # Roadmap step 10.5 (ADR-0040): the chain's last hop is the chat, and the human's devices.
    await post_alarm(deps, alarm)


async def _record_handled(
    deps: QueenDeps, handling: AlarmHandling, task_id: TaskId, action: str
) -> None:
    """Record `alarm.handled` for REBIND/RETRY_TASK and remember it for a later `alarm.resolved`."""
    alarm = Alarm.from_wire(handling.payload)
    handling.pending_alarms[task_id] = alarm
    await record_alarm_event(
        deps.trail, _identity(deps), deps.clock, alarm, "alarm.handled", action=action
    )


def _identity(deps: QueenDeps) -> CellIdentity:
    """Build the CellIdentity every alarm.* event this module records is stamped with."""
    return CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor=deps.identity.actor
    )


def _fallback_binding_key(deps: QueenDeps) -> str | None:
    """Return the `[llm.slots]` fallback key for the WORKER slot's own binding, if any."""
    by_key = {binding.key: binding for binding in deps.bindings}
    base = by_key.get(ModelSlot.WORKER.manifest_key)
    return base.fallback if base is not None else None


def _reason(payload: AlarmRaised) -> str:
    """Build the reason string every handler in this module records, from `payload`'s own kind."""
    return f"{payload.kind.value}: {payload.detail}"[:MAX_ALARM_REASON_CHARS]
