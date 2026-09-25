"""Quarantine one sub-bee: the one code path, and the three entry points that reach it.

ADR-0043, "Quarantine is one intervention": "One code path in wardens/ does it all: checkpoint,
cancel, kill the tracked process, revoke the bee's slice of the grant, taint every checkpoint,
Handoff and Nectar from that episode on, move the task to PAUSED, record warden.intervened and
memory.tainted." `quarantine_bee` is that path, in exactly that order, once the Guard's
`quarantine` enforcement point has allowed the order (`authority`): nothing is cut before the
checkpoint exists (`checkpoint`), and nothing is tainted before the bee is gone, so nothing it
writes can land after the labels. Cancel and kill are one call in this Warden (the supervisor of
one Cell): a sub-bee runs as a runtime this Warden tracks, and `retire_sub_bee` sets its stop flag
(its role is cancelled at its next tick, and a command it has in flight dies with it: a Cell
session's exec kills its child's process tree when cancelled), then reaps its task (cancelled
outright if it overruns the bounded grace), closes its link and frees its seat in the local pool.
Revoking its slice also withdraws the task's own grant from what this Warden will spawn under, so
nothing can restart the task until the Queen sends a fresh grant with a respawn the gate admits.
The taint goes through `hivemind.memory.taint.taint_memory` with `TaintSource.QUARANTINE`, the
bee's scope from its suspect episode on, the memory tables plus `WardenDeps.taint_ledgers` (the
Honey Store's Nectar ledger joins there in phase 7). Then the task is held (`_quarantined`, the
Warden's half of PAUSED; `report_paused` moves the Brood Chamber's half) and the Queen is told
(`SECURITY`). `carry_out` (the autopilot's `WardenAction.QUARANTINE`: a Queen-sent
`Intervene(QUARANTINE)` or this Warden's own policy row) and `quarantine_child`
(`Warden.intervene`) are the only callers.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    quarantine sub-package. Called by `hivemind.wardens.warden.Warden`'s tick dispatch and its
    `intervene`. Calls into `hivemind.memory.taint` (the one setter), `hivemind.supervision`,
    `hivemind.wardens.errors`, `hivemind.wardens.ticks.alarms` (retire_sub_bee, and the ESCALATE
    of an Alarm whose own quarantine cannot go ahead), `hivemind.wardens.ticks.trail_ship`, this
    package's own modules and waggle; never the reverse (no tick module imports this package).

Key invariants:
    - This is the only place memory is labelled QUARANTINE (tests/unit/memory/taint/
      test_only_setter.py binds `hivemind/wardens/quarantine` to that source).
    - Order: authorise, checkpoint, cancel and kill, revoke, record `warden.intervened`, taint,
      hold, tell. A refused order changes nothing but its `guard.denied` row.
    - An order for a bee or task already quarantined here changes nothing (idempotent).

See Also:
    - docs/adr/0043-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/tainted-memory.md for the label and its one clearer.
    - hivemind.wardens.quarantine.gate for the only way out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import CellIdentity
from hivemind.common.logging import get_logger
from hivemind.memory.taint import TaintReport, TaintScope, TaintSource, TaintStamp, taint_memory
from hivemind.supervision import Alarm, Quarantine, SupervisionError, record_alarm_event
from hivemind.wardens.autopilot import WardenAction
from hivemind.wardens.errors import UnknownSubBeeError
from hivemind.wardens.quarantine.authority import authorize
from hivemind.wardens.quarantine.checkpoint import memory_context, write_quarantine_checkpoint
from hivemind.wardens.quarantine.order import (
    QuarantineOrder,
    lever_order,
    own_policy_order,
    queen_order,
)
from hivemind.wardens.quarantine.record import QuarantineRecord
from hivemind.wardens.quarantine.report import (
    QUARANTINE_ACTION,
    raise_security_alarm,
    record_intervened,
    report_paused,
)
from hivemind.wardens.ticks.alarms import handle_alarm_action, retire_sub_bee
from hivemind.wardens.ticks.trail_ship import ship_trail_before_result
from waggle.ids import EventId, GrantId, WorkerId
from waggle.messages.supervision import AlarmRaised, Intervene

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

log = get_logger(__name__)

__all__ = ["carry_out", "quarantine_bee", "quarantine_child"]


async def quarantine_bee(warden: Warden, order: QuarantineOrder) -> QuarantineRecord | None:
    """Carry out `order`: the one quarantine code path (module docstring, in that order).

    Args:
        warden: The Warden supervising the bee; its private tables are read and written.
        order: The lever (bee, task, suspect episode, reason), who gave it and what they hold.

    Returns:
        The task's QuarantineRecord (the existing one when it was already quarantined here), or
        None when the `quarantine` point refused the order, which is then on the trail.
    """
    existing = _existing_record(warden, order.lever)
    if existing is not None:
        return existing  # Already done: a repeated order changes nothing (idempotent).
    sub_bee = _find_sub_bee(warden, order.lever)
    if not await authorize(warden, order, sub_bee) or sub_bee is None:
        return None
    # Checkpoint first, then cancel and kill: the Handoff exists before anything is cut.
    checkpoint = await write_quarantine_checkpoint(warden, sub_bee, order)
    await retire_sub_bee(warden, sub_bee)
    grant_id = _revoke_slice(warden, sub_bee)
    intervened = await record_intervened(warden, sub_bee, order, checkpoint, grant_id)
    report = await _taint(warden, sub_bee, order, intervened)
    record = QuarantineRecord(
        task_id=sub_bee.task_id,
        bee=sub_bee.worker_id,
        suspect_episode_id=order.lever.suspect_episode_id,
        checkpoint=checkpoint,
        intervened_event_id=intervened,
        tainted_count=len(report.tainted),
        clearance=sub_bee.assignment.clearance,
    )
    # Held here first, so nothing can respawn the task while the Queen is still being told.
    warden._quarantined[sub_bee.task_id] = record
    # The Queen acts on these reports at once: her trail gets this Cell's rows first.
    await ship_trail_before_result(warden)
    await report_paused(warden, record, sub_bee.attempt)
    await raise_security_alarm(warden, record)
    return record


async def carry_out(warden: Warden, payload: object, sub_bee: SubBee | None) -> None:
    """Carry out the autopilot's QUARANTINE: a Queen-sent order, or this Warden's own policy row.

    Args:
        warden: The Warden whose tick decided QUARANTINE.
        payload: The Queen's `Intervene(QUARANTINE)`, or the sub-bee Alarm a policy row matched.
        sub_bee: The sub-bee the item concerns, when the Warden knows one.
    """
    if isinstance(payload, Intervene):
        await _from_queen(warden, payload)
    elif isinstance(payload, AlarmRaised) and sub_bee is not None:
        await _from_own_policy(warden, sub_bee, payload)


async def quarantine_child(warden: Warden, child: str, lever: Quarantine) -> None:
    """Carry out `Warden.intervene(child, lever)`: this Warden quarantines its own sub-bee.

    Args:
        warden: This Warden, the orderer.
        child: The sub-bee's own worker id.
        lever: The Quarantine lever; its suspect episode and reason are the order's.

    Raises:
        UnknownSubBeeError: `child` names no current sub-bee (the Supervisor protocol's rule).
        SupervisionError: `lever` names a different bee than `child`.
    """
    if WorkerId(child) not in warden._sub_bees:
        raise UnknownSubBeeError(child)
    await quarantine_bee(warden, lever_order(warden, WorkerId(child), lever))


async def _from_queen(warden: Warden, intervene: Intervene) -> None:
    """Carry out the Queen's order; a malformed one is logged and changes nothing."""
    try:
        order = queen_order(warden, intervene)
    except SupervisionError as refused:
        # Only a peer that skipped Intervene's own validation gets here; acting on a guess would
        # quarantine a bee nobody named, so nothing is done.
        log.warning(
            "warden.quarantine.order_refused", warden_id=warden._warden_id, error=refused.code
        )
        return
    await quarantine_bee(warden, order)


async def _from_own_policy(warden: Warden, sub_bee: SubBee, alarm: AlarmRaised) -> None:
    """Quarantine `sub_bee` by this Warden's own policy row, or escalate when it cannot."""
    try:
        record = await quarantine_bee(warden, own_policy_order(warden, sub_bee, alarm))
    except SupervisionError:
        record = None  # No episode to quarantine from: the Queen decides instead, below.
    # Refused or impossible here: the next level up decides, exactly as for ESCALATE.
    if record is None:
        await handle_alarm_action(warden, sub_bee, alarm, WardenAction.ESCALATE, None)
        return
    # The Alarm that asked for it is handled at this level; the SECURITY Alarm tells the Queen.
    await record_alarm_event(
        warden._deps.trail,
        _cell_identity(warden),
        warden._deps.clock,
        Alarm.from_wire(alarm),
        "alarm.handled",
        action=QUARANTINE_ACTION,
    )


def _existing_record(warden: Warden, lever: Quarantine) -> QuarantineRecord | None:
    """Return the record of an earlier quarantine of the same task or bee, if any."""
    for record in warden._quarantined.values():
        if record.task_id == lever.task_id or record.bee == lever.bee:
            return record
    return None


def _find_sub_bee(warden: Warden, lever: Quarantine) -> SubBee | None:
    """Return the live sub-bee `lever` names, by bee first and else by task; None if none."""
    if lever.bee is not None:
        sub_bee = warden._sub_bees.get(lever.bee)
        # A bee and a task that disagree name two different things; neither is assumed.
        if sub_bee is not None and lever.task_id not in (None, sub_bee.task_id):
            return None
        return sub_bee
    return next((sb for sb in warden._sub_bees.values() if sb.task_id == lever.task_id), None)


def _revoke_slice(warden: Warden, sub_bee: SubBee) -> GrantId:
    """Withdraw what the bee's task could still spawn under; return the grant it held a slice of.

    `retire_sub_bee` already freed the bee's seat in the local pool. The task's own grant goes
    too, and any assignment parked for it, so no respawn can charge them before the Queen issues
    a fresh grant with a respawn the gate admits; a grant shared with other tasks stays theirs.
    """
    grant_id = sub_bee.assignment.grant_id
    grant = warden._grants.get(grant_id)
    if grant is not None and grant.task_id == sub_bee.task_id:
        del warden._grants[grant_id]
    warden._pending.pop(sub_bee.task_id, None)
    return grant_id


async def _taint(
    warden: Warden, sub_bee: SubBee, order: QuarantineOrder, cause: EventId
) -> TaintReport:
    """Label the bee's memory and its task's from the suspect episode on, QUARANTINE-sourced."""
    episode = order.lever.suspect_episode_id
    scope = TaintScope.for_bee(sub_bee.worker_id, episode, task_ids=(sub_bee.task_id,))
    stamp = TaintStamp(
        source=TaintSource.QUARANTINE,
        reason=f"Quarantine of {sub_bee.worker_id} on task {sub_bee.task_id} from episode "
        f"{episode}.",
        cause_event_id=cause,
    )
    return await taint_memory(scope, stamp, memory_context(warden), warden._deps.taint_ledgers)


def _cell_identity(warden: Warden) -> CellIdentity:
    """Build the identity this Warden's `alarm.*` rows are stamped with."""
    identity = warden._deps.identity
    return CellIdentity(hive_id=identity.hive_id, node_id=identity.node_id, actor=identity.actor)
