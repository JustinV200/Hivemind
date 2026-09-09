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
at every hop... so no level handles it twice").

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's ticks
    sub-package. These are `hivemind.wardens.warden.Warden`'s own delegates (see
    `hivemind.wardens.ticks.assign`'s own module docstring for why). Calls into `hivemind.cell`
    (HoneyClearance), `hivemind.forage.slots` (ModelSlot), `hivemind.wardens.spawn`
    (WardenCellContext, spawn_sub_bee) and waggle only.

Key invariants:
    - `retire_sub_bee` closes the old sub-bee's link and cancels its runtime task (if still running)
      exactly once per Alarm handled; a sub-bee is removed from `warden._sub_bees` before any
      respawn, so its old worker id is never briefly aliased to two runtime tasks.
    - `_rebind` never respawns on the sub-bee's own current binding: it always picks a different
      entry from the grant's `allowed`, or escalates when none is left.

See Also:
    - docs/adr/0012-wardens-alarms-and-the-escalation-chain.md for the escalation chain this
      module implements the Warden's own hop of.
    - .claude/roadmap.md step 3.19's own dispatch map for the four actions this module handles.
    - hivemind.wardens.autopilot.table for decide, which maps a PolicyAction to one of these four.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.forage.slots import ModelSlot
from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.spawn import WardenCellContext, spawn_sub_bee
from waggle.envelope import wrap
from waggle.ids import TaskId, new_alarm_id
from waggle.messages import AlarmSeverity
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import TaskOutcome, TaskResult

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden
    from waggle.messages.forage import GrantIssued

MAX_REASON_CHARS = 2_000  # Mirrors waggle.messages.task.reports.MAX_SUMMARY_CHARS's own bound.

__all__ = ["MAX_REASON_CHARS", "handle_alarm_action", "retire_sub_bee", "send_alarm_to_queen"]


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
    if action is WardenAction.RETRY:
        await _respawn(warden, sub_bee, alarm, binding_override=None)
    elif action is WardenAction.REBIND:
        await _rebind(warden, sub_bee, alarm, binding)
    elif action is WardenAction.ESCALATE:
        await _escalate(warden, sub_bee, alarm)
    elif action is WardenAction.CANCEL_TASK:
        await _cancel_task(warden, sub_bee, alarm)


async def _respawn(
    warden: Warden, sub_bee: SubBee, alarm: AlarmRaised, *, binding_override: str | None
) -> None:
    """Retire `sub_bee` and start a fresh one for the same task, attempt+1, from its last Handoff.

    Escalates instead when the grant this task ran under, or this Warden's own Cell/lease/
    session, is no longer available to respawn onto.
    """
    grant = warden._grants.get(sub_bee.assignment.grant_id)
    if grant is None or warden._cell is None or warden._lease is None or warden._session is None:
        await _escalate(warden, sub_bee, alarm)
        return
    await retire_sub_bee(warden, sub_bee)
    new_assignment = sub_bee.assignment.model_copy(
        update={"attempt": sub_bee.attempt + 1, "resume_from": sub_bee.last_handoff}
    )
    ctx = WardenCellContext(
        warden_id=warden._warden_id,
        deps=warden._deps,
        ceiling=warden._ceiling,
        cell=warden._cell,
        lease=warden._lease,
        session=warden._session,
    )
    new_sub_bee = await spawn_sub_bee(ctx, new_assignment, grant, binding_override)
    warden._sub_bees[new_sub_bee.worker_id] = new_sub_bee
    warden._sub_bee_iters[new_sub_bee.worker_id] = new_sub_bee.link.receive()


async def _rebind(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised, binding: str | None) -> None:
    """Respawn on `binding`, or the next allowed-bindings entry; escalate when none is left."""
    grant = warden._grants.get(sub_bee.assignment.grant_id)
    target = binding or (None if grant is None else _next_allowed_binding(sub_bee, grant))
    if target is None:
        await _escalate(warden, sub_bee, alarm)
        return
    await _respawn(warden, sub_bee, alarm, binding_override=target)


def _next_allowed_binding(sub_bee: SubBee, grant: GrantIssued) -> str | None:
    """Return the first allowed-binding manifest key that differs from `sub_bee`'s own."""
    for allowed in grant.allowed:
        key = ModelSlot.from_wire(allowed.slot).manifest_key
        if key != sub_bee.binding:
            return key
    return None


async def _cancel_task(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised) -> None:
    """Retire `sub_bee` and report its task FAILED to the Queen."""
    await retire_sub_bee(warden, sub_bee)
    reason = f"Cancelled after {alarm.kind.value}: {alarm.detail}"[:MAX_REASON_CHARS]
    await _send_result(warden, sub_bee, outcome=TaskOutcome.FAILED, reason=reason)


async def _escalate(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised) -> None:
    """Forward `alarm` to the Queen unchanged apart from an incremented attempt count."""
    del sub_bee  # Not needed to forward: the Alarm already carries every reference it needs.
    forwarded = alarm.model_copy(update={"attempts": alarm.attempts + 1})
    await _send_to_queen(warden, forwarded)


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
    await _send_to_queen(warden, alarm)


async def retire_sub_bee(warden: Warden, sub_bee: SubBee) -> None:
    """Cancel `sub_bee`'s runtime task, close its link, and drop it from the Warden's tables."""
    if not sub_bee.runtime_task.done():
        sub_bee.runtime_task.cancel()
    warden._sub_bees.pop(sub_bee.worker_id, None)
    warden._sub_bee_iters.pop(sub_bee.worker_id, None)
    warden._receive_tasks.pop(sub_bee.worker_id, None)
    warden._local_pool.release()
    await sub_bee.link.close()


async def _send_to_queen(warden: Warden, alarm: AlarmRaised) -> None:
    """Wrap and send `alarm` to the Queen over this Warden's own queen link."""
    await warden._deps.queen_link.send(wrap(alarm, warden._deps.hop, clock=warden._deps.clock))


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
    await warden._deps.queen_link.send(wrap(result, warden._deps.hop, clock=warden._deps.clock))
