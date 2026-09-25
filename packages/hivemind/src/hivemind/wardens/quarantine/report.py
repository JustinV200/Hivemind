"""Record a quarantine on the trail and tell the Queen: `warden.intervened`, PAUSED and SECURITY.

Three reports close the one quarantine code path (roadmap step 10.6c, ADR-0043). `record_intervened`
writes `warden.intervened`, the audit row of the whole intervention (the task, the bee, the action,
the suspect episode, the checkpoint and the revoked grant, ids only); it is recorded before the
bee's memory is tainted so every `memory.tainted` row can name it as its cause. `report_paused`
sends the Queen (the orchestrator, who alone holds the Brood Chamber, the task store) a
`task.progress` at stage `PAUSED`: a Warden never writes the chamber itself (a Virtual Cell's Warden
runs inside its container, ADR-0027), so this is how the task moves to PAUSED there, through the
wire's own "Warden -> Queen: report a stage change". `raise_security_alarm` is "the Queen is told
either way": a `SECURITY` Alarm (Waggle 1.9), which every shipped policy row sends up the chain and
on to the human, since a report the Queen acts on reaches the human as an Alarm (roadmap 10.6).

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    quarantine sub-package. Called by `hivemind.wardens.quarantine.path` (all three) and by
    `hivemind.wardens.quarantine.gate` (`report_paused`, when a respawn is refused). Calls into
    `hivemind.cell` (CellIdentity), `hivemind.pheromone` (WardenEvent), `hivemind.supervision`
    (Alarm, record_alarm_event) and waggle only.

Key invariants:
    - No report carries text a bee wrote: ids, counts and enum values only, plus the order's own
      bounded reason on the audit row.
    - The Alarm's `alarm.raised` row is on the trail before the Alarm leaves the link, as for
      every Alarm a Warden raises itself.

See Also:
    - hivemind.guard.policy.catalogue, where `warden.intervened` is authorised at `quarantine`.
    - docs/waggle/spec.md section 8.3 for AlarmKind.SECURITY and TaskStage on task.progress.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.cell import CellIdentity
from hivemind.pheromone import MAX_PAYLOAD_STRING_CHARS, WardenEvent
from hivemind.supervision import Alarm, record_alarm_event
from hivemind.wardens.quarantine.order import QuarantineOrder
from hivemind.wardens.quarantine.record import QuarantineRecord
from waggle.envelope import wrap
from waggle.ids import EventId, GrantId, new_alarm_id, new_event_id
from waggle.messages import AlarmSeverity, HandoffRef
from waggle.messages.base import WaggleMessage
from waggle.messages.supervision import AlarmContext, AlarmKind, AlarmRaised
from waggle.messages.task import TaskProgress, TaskStage

if TYPE_CHECKING:
    from hivemind.wardens.spawn.sub_bee import SubBee
    from hivemind.wardens.warden import Warden

INTERVENED_KIND = "warden.intervened"  # The one trail kind a quarantine records for itself.
QUARANTINE_ACTION = "QUARANTINE"  # The `action` a `warden.intervened` row names.

__all__ = [
    "INTERVENED_KIND",
    "QUARANTINE_ACTION",
    "raise_security_alarm",
    "record_intervened",
    "report_paused",
]


async def record_intervened(
    warden: Warden,
    sub_bee: SubBee,
    order: QuarantineOrder,
    checkpoint: HandoffRef,
    grant_id: GrantId,
) -> EventId:
    """Record `warden.intervened` for a quarantine that has checkpointed, stopped and revoked.

    Args:
        warden: The Warden that carried it out; the row's subject.
        sub_bee: The quarantined bee.
        order: The order, for who gave it, the suspect episode, the reason and any Alarm.
        checkpoint: The Handoff written before anything was cut.
        grant_id: The grant whose slice the bee held.

    Returns:
        The event's id, which every `memory.tainted` row of this quarantine names as its cause.
    """
    deps = warden._deps
    payload: dict[str, JsonValue] = {
        "action": QUARANTINE_ACTION,
        "task_id": sub_bee.task_id,
        "worker_id": sub_bee.worker_id,
        "suspect_episode_id": order.lever.suspect_episode_id,
        "handoff_event_id": checkpoint.event_id,
        "grant_id": grant_id,
        "ordered_by": order.ordered_by.kind.value,
        "reason": order.lever.reason[:MAX_PAYLOAD_STRING_CHARS],
    }
    # The Alarm the order answers, when there is one, links the two on the trail.
    if order.alarm_id is not None:
        payload["alarm_id"] = order.alarm_id
    event = WardenEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=deps.identity.actor,
        kind=INTERVENED_KIND,
        subject_id=warden._warden_id,
        payload=payload,
    )
    await deps.trail.record(event)
    return event.id


async def report_paused(warden: Warden, record: QuarantineRecord, attempt: int) -> None:
    """Tell the Queen the task is held: a `task.progress` at stage PAUSED.

    Args:
        warden: The Warden holding the task.
        record: The quarantine that holds it.
        attempt: The attempt the report echoes (the quarantined one, or a refused respawn's).
    """
    summary = (
        f"Held: {record.bee} is quarantined and its memory from episode "
        f"{record.suspect_episode_id} on is tainted; the task resumes only from checkpoint "
        f"{record.checkpoint.event_id} once a judge has cleared it."
    )
    progress = TaskProgress(
        task_id=record.task_id,
        attempt=attempt,
        stage=TaskStage.PAUSED,
        summary=summary,
        clearance=record.clearance,
        fraction_done=None,
        handoff=None,
    )
    await _send(warden, progress)


async def raise_security_alarm(warden: Warden, record: QuarantineRecord) -> None:
    """Tell the Queen a bee was quarantined: a SECURITY Alarm this Warden raises itself.

    Args:
        warden: The Warden that quarantined it; the Alarm's origin.
        record: The quarantine: the task, the bee, the audit row and the checkpoint.
    """
    deps = warden._deps
    alarm = AlarmRaised(
        alarm_id=new_alarm_id(deps.clock),
        kind=AlarmKind.SECURITY,
        severity=AlarmSeverity.CRITICAL,
        origin=warden._warden_id,
        attempts=0,
        raised_at=deps.clock.now(),
        context=AlarmContext(
            task_id=record.task_id,
            cell_id=warden._cell.id if warden._cell is not None else None,
            worker_id=record.bee,
            event_id=record.intervened_event_id,
            handoff=record.checkpoint,
        ),
        detail=f"Quarantined {record.bee} on task {record.task_id}: {record.tainted_count} memory "
        f"items from episode {record.suspect_episode_id} on are tainted.",
        clearance=record.clearance,
        reason="A quarantine always reaches the Queen, and through her the human.",
    )
    # Opened here, where the Alarm is raised, so the Queen's later hops hang off this row.
    identity = CellIdentity(
        hive_id=deps.identity.hive_id, node_id=deps.identity.node_id, actor=deps.identity.actor
    )
    await record_alarm_event(
        deps.trail, identity, deps.clock, Alarm.from_wire(alarm), "alarm.raised"
    )
    await _send(warden, alarm)


async def _send(warden: Warden, message: WaggleMessage) -> None:
    """Wrap `message` and send it to the Queen over this Warden's own link."""
    deps = warden._deps
    await deps.queen_link.send(wrap(message, deps.hop, clock=deps.clock))
