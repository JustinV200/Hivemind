"""Fold the trail's alarm.* events into the Alarms still open, each placed on a Cell when it can be.

An Alarm is a bee's escalation of something it could not fix (codingrules 8.8), and every hop of
its climb is an ``alarm.*`` trail event sharing the Alarm's id: ``raised``, ``handled``,
``escalated`` and ``resolved``. Nothing durable holds the Alarm itself, so the Observation Hive
reads what is open by folding those events: an Alarm is open from its first event until a
``resolved`` one. Each event's payload names the Alarm's kind, severity and task but not its Cell,
so the fold places it through the task's Cell, else through the Warden that recorded it. The
Alarm's ``detail`` sentence is deliberately left out: it is free text a bee wrote about the failing
work, so it stays off the ``observe`` views.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.observation.api``. Used by the
    fleet, Cell page, Attendant and summary builders and by the metrics snapshot. Pure: calls into
    the trail's event model, the Alarm severity enum and pydantic only.

Key invariants:
    - Pure: the events are handed in, oldest first; nothing is read or written here.
    - An event whose kind or severity this Hive does not know is skipped, never guessed at.

See Also:
    - hivemind.supervision.alarm_trail for the writer of every event folded here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.pheromone import PheromoneEvent
from hivemind.supervision import AlarmSeverity
from waggle.messages.base import AlarmIdField, CellIdField, TaskIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every view here: immutable, no strays.

ALARM_FAMILY = "alarm"  # The trail family every Alarm hop is recorded under.
_RAISED = "alarm.raised"  # The hop that opens an Alarm.
_ESCALATED = "alarm.escalated"  # The hop that puts it in front of the human.
_RESOLVED = "alarm.resolved"  # The hop that closes it.

__all__ = ["ALARM_FAMILY", "OpenAlarmView", "alarms_on_cell", "open_alarms"]


class OpenAlarmView(BaseModel):
    """One Alarm that is still open, without the sentence a bee wrote about it (observe)."""

    model_config = _CONFIG

    id: AlarmIdField = Field(description="The Alarm's id, identical at every level.")
    kind: str = Field(description="What went wrong (WORKER_CRASHED, GRANT_EXCEEDED ...).")
    severity: AlarmSeverity = Field(description="INFO, WARNING or CRITICAL.")
    task_id: TaskIdField | None = Field(description="The task it concerns, if any.")
    cell_id: CellIdField | None = Field(
        description="The Cell it is placed on through its task or its recording Warden; null "
        "when neither can be resolved."
    )
    raised_at: datetime = Field(
        description="When its first event in the folded window happened (its raising, unless "
        "that is older than the window)."
    )
    escalated: bool = Field(description="It has reached the human.")


def open_alarms(
    events: Sequence[PheromoneEvent],
    cell_by_task: Mapping[str, str],
    cell_by_warden: Mapping[str, str],
) -> tuple[OpenAlarmView, ...]:
    """Fold ``alarm.*`` events into the Alarms with no ``resolved`` hop yet.

    Args:
        events: The ``alarm.*`` trail events, in any order (they are sorted oldest first here).
        cell_by_task: Each known task's Cell id, to place an Alarm through its task.
        cell_by_warden: Each attached Warden's Cell id, the fallback placement.

    Returns:
        The open Alarms, oldest first.
    """
    open_by_id: dict[str, OpenAlarmView] = {}
    closed: set[str] = set()
    # Oldest first, so a resolved hop closes exactly the Alarm its earlier hops opened.
    for event in sorted(events, key=lambda hop: hop.at):
        if event.family != ALARM_FAMILY or event.subject_id in closed:
            continue
        if event.kind == _RESOLVED:
            closed.add(event.subject_id)
            open_by_id.pop(event.subject_id, None)
            continue
        view = open_by_id.get(event.subject_id) or _view(event, cell_by_task, cell_by_warden)
        if view is None:
            continue
        # An escalation is the one later hop that changes what the view says.
        escalated = view.escalated or event.kind == _ESCALATED
        open_by_id[event.subject_id] = view.model_copy(update={"escalated": escalated})
    return tuple(sorted(open_by_id.values(), key=lambda alarm: (alarm.raised_at, alarm.id)))


def alarms_on_cell(alarms: Sequence[OpenAlarmView], cell_id: str) -> tuple[OpenAlarmView, ...]:
    """Pick the open Alarms placed on one Cell.

    Args:
        alarms: Open Alarms, from ``open_alarms``.
        cell_id: The Cell.

    Returns:
        Those whose placement is that Cell, in the order given.
    """
    return tuple(alarm for alarm in alarms if alarm.cell_id == cell_id)


def _view(
    event: PheromoneEvent, cell_by_task: Mapping[str, str], cell_by_warden: Mapping[str, str]
) -> OpenAlarmView | None:
    """Build an Alarm's view from the first hop seen, or None when its payload is unreadable."""
    payload = event.payload
    kind, severity, task = payload.get("kind"), payload.get("severity"), payload.get("task_id")
    try:
        parsed = AlarmSeverity(str(severity))
    except ValueError:
        return None
    if not isinstance(kind, str):
        return None
    task_id = task if isinstance(task, str) else None
    # The task's Cell first (the work that failed); the recording Warden's Cell is the fallback.
    cell = cell_by_task.get(task_id) if task_id is not None else None
    cell = cell or cell_by_warden.get(event.actor)
    return OpenAlarmView(
        id=event.subject_id,
        kind=kind,
        severity=parsed,
        task_id=task_id,
        cell_id=cell,
        raised_at=event.at,
        escalated=event.kind == _ESCALATED,
    )
