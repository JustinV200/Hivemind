"""Define record_alarm_event: write one alarm.* PheromoneEvent for an Alarm's own lifecycle step.

Codingrules section 8.8: "Every hop is a trail event carrying the same alarm id so no level
handles it twice." `hivemind.pheromone.events.families.AlarmEvent` already names the four kinds
(`alarm.raised`, `alarm.handled`, `alarm.escalated`, `alarm.resolved`) an Alarm's own chain moves
through, but nothing in the shipped Warden or Queen ever wrote one -- every hop only ever recorded
its OWN `warden.*`/`queen.*` bookkeeping, so the Observation Hive and a human auditor could never
see an Alarm's own path up the tree. `record_alarm_event` is the one place that gap closes: every
caller (a Warden raising, handling or escalating an Alarm; the Queen handling, escalating to the
human, or resolving one) passes the same `Alarm` value and the trail records `subject_id =
alarm.id` with a payload of ids and enums only (codingrules section 12: never the detail text,
which already travels on the Alarm itself for whoever reads it back through the Observation Hive).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Called
    by `hivemind.wardens.ticks.alarms`, `hivemind.wardens.ticks.heartbeat`, `hivemind.wardens.
    ticks.results` and `hivemind.queen.ticks.alarms`, `hivemind.queen.queen` -- every place an
    Alarm is raised, handled, escalated or resolved. Calls into `hivemind.cell` (CellIdentity, the
    same hive_id/node_id/actor bundle `hivemind.supervision.capping.gate.GateDeps` already stamps
    its own trail events with) and `hivemind.pheromone` (AlarmEvent, PheromoneTrail) only.

Key invariants:
    - The payload never carries `Alarm.detail` or any other free text; only `alarm.kind.value`,
      `alarm.severity.value`, `alarm.attempts` and whatever ids/enum values a caller adds.
    - `identity` is a `hivemind.cell.CellIdentity` rather than `hivemind.memory.MemoryIdentity`:
      both share the exact hive_id/node_id/actor shape, but `cell` is the one Layer 2 sibling
      `supervision` is documented to import from (codingrules section 4's own corollary), while
      `memory` is not.

See Also:
    - .claude/codingrules.md section 8.8 for "every hop is a trail event carrying the same alarm
      id so no level handles it twice".
    - .claude/codingrules.md section 12 for the ids-and-enums-only payload rule this module follows.
    - hivemind.pheromone.events.families for AlarmEvent and its own four-kind vocabulary.
    - hivemind.supervision.alarm for Alarm, the value every call here is built from.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.cell import CellIdentity
from hivemind.pheromone import AlarmEvent, PheromoneTrail
from hivemind.supervision.alarm import Alarm
from waggle.clock import Clock
from waggle.ids import new_event_id

__all__ = ["record_alarm_event"]


async def record_alarm_event(
    trail: PheromoneTrail,
    identity: CellIdentity,
    clock: Clock,
    alarm: Alarm,
    kind: str,
    **payload: JsonValue,
) -> None:
    """Build and record one alarm.* AlarmEvent for `alarm`'s own escalation-chain step.

    Args:
        trail: The Pheromone Trail this event is recorded to.
        identity: The hive, node and actor stamping this event; the caller's own `CellIdentity`.
        clock: Source of the event's own minted id and timestamp.
        alarm: The Alarm this step concerns; `subject_id` is always `alarm.id` so every hop for
            the same Alarm shares one trail thread.
        kind: One of `hivemind.pheromone.AlarmEvent.KINDS` ("alarm.raised", "alarm.handled",
            "alarm.escalated" or "alarm.resolved").
        **payload: Extra ids or enum values for this one step (e.g. the action a Warden or the
            Queen took); never free text (codingrules section 12).
    """
    event = AlarmEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor=identity.actor,
        kind=kind,
        subject_id=alarm.id,
        payload={
            "kind": alarm.kind.value,
            "severity": alarm.severity.value,
            "attempts": alarm.attempts,
            **payload,
        },
    )
    await trail.record(event)
