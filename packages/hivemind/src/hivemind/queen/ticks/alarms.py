"""Define handle_alarm: what REBIND, ESCALATE_TO_HUMAN, RETRY_TASK and FAIL_TASK do for an Alarm.

Roadmap step 3.20's own dispatch map: "escalated AlarmRaised -> policy + attempts: RETRY_TASK
(re-dispatch, attempt+1), REBIND (Intervene(REBIND, task_id, slot=<the fallback binding key from
deps.bindings for the task's slot chain>) to the Warden... when no fallback exists ->
ESCALATE_TO_HUMAN), FAIL_TASK (chamber.fail), ESCALATE_TO_HUMAN (human_inbox.add_alarm and
alarm.escalated is the supervision owner's event: record what you own only, i.e. queen.decided)."
`RETRY_TASK` and `FAIL_TASK` reuse `hivemind.queen.ticks.results.retry_task`/`.fail_task`, the same
two functions a `TaskResult(FAILED)` drives, since "retry this task" means the same chamber calls
regardless of which report triggered the decision. `REBIND`'s own wire message is built through
`hivemind.supervision.intervention.Rebind`/`to_wire`, the same six-lever `Intervention` machinery
`Queen.intervene` (the `Supervisor` protocol method) already uses -- not a hand-rolled `Intervene`
-- because the wire `Intervene.slot` field only ever carries a `ModelSlot`'s own UPPER_SNAKE wire
value (`waggle.messages.base.SLOT_PATTERN`), never a lowercase `[llm.slots]` named-binding key such
as `"local_worker"`; the resolved fallback *key* is recorded on the trail instead (this dispatch's
own report flags the gap this works around, since neither `wardens/**` nor `waggle/**` is this
dispatch's to touch).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.queen.Queen`'s own tick dispatch, once per decided
    `REBIND`/`ESCALATE_TO_HUMAN`/`RETRY_TASK`/`FAIL_TASK` for an `AlarmRaised`. Calls into
    `hivemind.cell` (CellIdentity), `hivemind.forage.slots` (ModelSlot), `hivemind.queen.autopilot`
    (QueenAction), `hivemind.queen.deps` (QueenDeps, WardenLink), `hivemind.queen.human_inbox`
    (HumanInbox), `hivemind.queen.ticks.results` (fail_task, retry_task), `hivemind.queen.trail`
    (record_event), `hivemind.supervision` (Alarm, record_alarm_event, intervention.Rebind,
    to_wire) and waggle only.

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

from collections.abc import MutableMapping, Sequence
from dataclasses import dataclass

from hivemind.cell import CellIdentity
from hivemind.forage.slots import ModelSlot
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.ticks.results import fail_task, retry_task
from hivemind.queen.trail import record_event
from hivemind.supervision import Alarm, record_alarm_event
from hivemind.supervision.intervention import Rebind, to_wire
from waggle.envelope import wrap
from waggle.ids import TaskId, WardenId
from waggle.messages.supervision import AlarmRaised, Intervene

MAX_ALARM_REASON_CHARS = 2_000  # Matches waggle.messages.supervision.alarms.MAX_DETAIL_CHARS.

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
    """Carry out REBIND, ESCALATE_TO_HUMAN, RETRY_TASK or FAIL_TASK for an escalated Alarm.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached.
        handling: The human inbox, the forwarding Warden, the Alarm, the decided action and the
            Queen's own shared attempt counter (module docstring: the chamber's `Task.attempt`
            cannot track a RUNNING task's own retries, so the Queen tracks it instead).
    """
    payload, action = handling.payload, handling.action
    task_id = payload.context.task_id
    if action is QueenAction.RETRY_TASK and task_id is not None:
        await _record_handled(deps, handling, task_id, "RETRY_TASK")
        next_attempt = handling.attempts.get(task_id, 1) + 1
        handling.attempts[task_id] = next_attempt
        await retry_task(deps, wardens, task_id, next_attempt)
    elif action is QueenAction.FAIL_TASK and task_id is not None:
        alarm = Alarm.from_wire(payload)
        await record_alarm_event(
            deps.trail, _identity(deps), deps.clock, alarm, "alarm.handled", action="FAIL_TASK"
        )
        handling.pending_alarms.pop(task_id, None)  # A failed task never resolves its own Alarm.
        await fail_task(deps, task_id, _reason(payload))
    elif action is QueenAction.REBIND:
        await _rebind(deps, wardens, handling)
    else:
        # ESCALATE_TO_HUMAN, or RETRY_TASK/FAIL_TASK for an Alarm naming no task: escalate rather
        # than silently dropping an Alarm this table decided needs a human.
        await _escalate(deps, handling.human_inbox, payload)


async def _rebind(deps: QueenDeps, wardens: Sequence[WardenLink], handling: AlarmHandling) -> None:
    """Send Intervene(REBIND) to the forwarding Warden, or ESCALATE_TO_HUMAN with no fallback."""
    payload = handling.payload
    task_id = payload.context.task_id
    link = next((w for w in wardens if w.warden_id == handling.warden_id), None)
    fallback_key = _fallback_binding_key(deps)
    if task_id is None or link is None or fallback_key is None:
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
    await link.transport.send(wrap(message, link.hop, clock=deps.clock))
    await record_event(deps, "queen.decided", task_id, action="REBIND", binding=fallback_key)
    await _record_handled(deps, handling, task_id, "REBIND")


async def _escalate(deps: QueenDeps, human_inbox: HumanInbox, payload: AlarmRaised) -> None:
    """Add `payload` to the human inbox and record the Queen's own decision to escalate."""
    alarm = Alarm.from_wire(payload)
    human_inbox.add_alarm(alarm)
    subject = payload.context.task_id or deps.identity.hive_id
    await record_event(
        deps, "queen.decided", subject, action="ESCALATE_TO_HUMAN", alarm_id=alarm.id
    )
    await record_alarm_event(deps.trail, _identity(deps), deps.clock, alarm, "alarm.escalated")


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
