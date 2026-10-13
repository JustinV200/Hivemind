"""Test hivemind.observation.api.alarms: open Alarms folded from the trail's alarm.* events.

An Alarm is open from its first event until a resolved one; its Cell is found through its task, else
through the Warden that recorded it; an escalation is remembered; an unreadable event is skipped;
and the Alarm's free-text detail never reaches the view.

Fits into the Hive:
    Mirrors src/hivemind/observation/api/alarms.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.observation import after, make_event
from pydantic import JsonValue

from hivemind.observation import alarms_on_cell, open_alarms
from hivemind.supervision import AlarmSeverity
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_cell_id, new_task_id, new_warden_id

_CLOCK = FakeClock()


def _payload(task_id: str | None = None, severity: str = "WARNING") -> dict[str, JsonValue]:
    """The payload every alarm hop carries, detail text included."""
    return {
        "kind": "WORKER_FAILED",
        "severity": severity,
        "attempts": 1,
        "task_id": task_id,
        "detail": "The tool call raised an exception.",
    }


def test_no_events_leave_no_open_alarm() -> None:
    assert open_alarms([], {}, {}) == ()


def test_an_alarm_is_open_until_a_resolved_hop_closes_it() -> None:
    open_id, closed_id = new_alarm_id(_CLOCK), new_alarm_id(_CLOCK)
    events = [
        make_event("alarm.raised", open_id, after(1), payload=_payload()),
        make_event("alarm.raised", closed_id, after(2), payload=_payload()),
        make_event("alarm.handled", closed_id, after(3), payload=_payload()),
        make_event("alarm.resolved", closed_id, after(4), payload=_payload()),
    ]

    alarms = open_alarms(events, {}, {})

    assert [alarm.id for alarm in alarms] == [open_id]
    assert (alarms[0].kind, alarms[0].severity) == ("WORKER_FAILED", AlarmSeverity.WARNING)
    assert alarms[0].raised_at == after(1)
    assert not alarms[0].escalated


def test_events_are_folded_oldest_first_whatever_order_they_arrive_in() -> None:
    alarm_id = new_alarm_id(_CLOCK)
    resolved = make_event("alarm.resolved", alarm_id, after(5), payload=_payload())
    raised = make_event("alarm.raised", alarm_id, after(1), payload=_payload())

    assert open_alarms([resolved, raised], {}, {}) == ()


def test_an_escalation_is_remembered_and_the_detail_text_never_surfaces() -> None:
    alarm_id = new_alarm_id(_CLOCK)
    events = [
        make_event("alarm.raised", alarm_id, after(1), payload=_payload(severity="CRITICAL")),
        make_event("alarm.escalated", alarm_id, after(2), payload=_payload(severity="CRITICAL")),
    ]

    [alarm] = open_alarms(events, {}, {})

    assert alarm.escalated
    assert alarm.severity is AlarmSeverity.CRITICAL
    assert "detail" not in alarm.model_dump()


def test_an_alarm_is_placed_on_its_tasks_cell_else_its_wardens() -> None:
    task_id, warden_id = new_task_id(_CLOCK), new_warden_id(_CLOCK)
    task_cell, warden_cell = new_cell_id(_CLOCK), new_cell_id(_CLOCK)
    through_task, through_warden, nowhere = (new_alarm_id(_CLOCK) for _ in range(3))
    events = [
        make_event(
            "alarm.raised", through_task, after(1), actor=warden_id, payload=_payload(task_id)
        ),
        make_event("alarm.raised", through_warden, after(2), actor=warden_id, payload=_payload()),
        make_event("alarm.raised", nowhere, after(3), payload=_payload()),
    ]

    alarms = open_alarms(events, {task_id: task_cell}, {warden_id: warden_cell})

    assert [alarm.cell_id for alarm in alarms] == [task_cell, warden_cell, None]
    assert [alarm.id for alarm in alarms_on_cell(alarms, warden_cell)] == [through_warden]


def test_an_event_with_an_unknown_severity_is_skipped_not_guessed() -> None:
    alarm_id = new_alarm_id(_CLOCK)
    event = make_event("alarm.raised", alarm_id, after(1), payload=_payload(severity="DIRE"))

    assert open_alarms([event], {}, {}) == ()
