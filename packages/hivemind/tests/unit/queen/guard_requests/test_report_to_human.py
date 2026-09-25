"""Tests for `report_to_human`: a CRITICAL Guard report reaches the human once, pushed and durable.

ADR-0043: a report at CRITICAL confidence is shown to the human whatever it recommends. The
Queen's door shows a CRITICAL finding that asks for nothing (a raised audit rate, an Entrance
reduce order, an observation) as a SECURITY Alarm naming the report, pushed to every device and
committed before the call returns; showing is idempotent by report id, across a new door and a
fresh trail connection alike, because the proof is the durable `alarm.escalated` row. A CRITICAL
request is filed, not shown: the one Alarm the human gets for it is the Queen's, when she acts.

Fits into the Hive:
    Mirrors src/hivemind/queen/guard_requests/door.py (report_to_human) and show.py (codingrules
    section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.guard.report for the GuardRequestDoor contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.human import RecordingHumanChannel
from builders.isolation import isolation_site, make_guard_report
from builders.queen import make_queen_deps

from hivemind.common.sqlite import connect
from hivemind.guard import GuardAction, GuardConfidence
from hivemind.pheromone import SqlitePheromoneTrail, TrailQuery
from hivemind.queen import Queen
from hivemind.queen.chat import ChatKind, ChatQuery
from hivemind.queen.guard_requests import (
    GuardDeps,
    QueenGuardDoor,
    guard_items,
    report_alarm_id,
)
from hivemind.queen.guard_requests.decision import decide_guard_item
from hivemind.queen.human_inbox import HumanInbox
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import FakeClock

_ESCALATED = "alarm.escalated"


async def test_a_critical_finding_is_pushed_to_every_device_once() -> None:
    clock = FakeClock()
    channel = RecordingHumanChannel()
    deps, _link, warden_end = make_queen_deps(clock, human_channel=channel)
    inbox = HumanInbox()
    report = make_guard_report(
        clock,
        cell_id=None,
        recommended=GuardAction.RAISE_AUDIT_RATE,
        confidence=GuardConfidence.CRITICAL,
    )
    door = QueenGuardDoor(deps, inbox)

    await door.report_to_human(report)
    await door.report_to_human(report)  # A repeat shows nothing.

    [alarm] = inbox.alarms
    assert alarm.id == report_alarm_id(report.id)
    assert (alarm.kind, alarm.severity) == (AlarmKind.SECURITY, AlarmSeverity.CRITICAL)
    assert report.id in alarm.detail
    assert channel.names().count("alarm_raised") == 1  # Pushed to every device, once.
    lines = await deps.chat.read(ChatQuery())
    assert [line.ref for line in lines if line.kind is ChatKind.ALARM] == [alarm.id]
    escalated = await deps.trail.query(TrailQuery(kind=_ESCALATED, subject_id=alarm.id))
    assert len(escalated) == 1
    await warden_end.close()


async def test_showing_is_idempotent_across_a_restart(tmp_path: Path) -> None:
    clock = FakeClock()
    db = tmp_path / "hive.db"
    first_trail = await SqlitePheromoneTrail.create(connect(db), clock)
    deps, _link, warden_end = make_queen_deps(clock, trail=first_trail)
    report = make_guard_report(
        clock,
        cell_id=None,
        recommended=GuardAction.OBSERVE,
        confidence=GuardConfidence.CRITICAL,
    )
    await QueenGuardDoor(deps, HumanInbox()).report_to_human(report)

    # A new process: a fresh connection to the same file, a new door, an empty human inbox.
    second_trail = await SqlitePheromoneTrail.create(connect(db), clock)
    restarted, _other_link, other_end = make_queen_deps(clock, trail=second_trail)
    inbox = HumanInbox()
    await QueenGuardDoor(restarted, inbox).report_to_human(report)

    assert inbox.alarms == ()
    alarm_id = report_alarm_id(report.id)
    assert len(await second_trail.query(TrailQuery(kind=_ESCALATED, subject_id=alarm_id))) == 1
    await warden_end.close()
    await other_end.close()


async def test_a_report_below_critical_is_refused() -> None:
    deps, _link, warden_end = make_queen_deps()
    report = make_guard_report(
        cell_id=None, recommended=GuardAction.OBSERVE, confidence=GuardConfidence.HIGH
    )

    with pytest.raises(ValueError, match="only a CRITICAL report"):
        await QueenGuardDoor(deps, HumanInbox()).report_to_human(report)
    assert await deps.trail.query(TrailQuery(kind=_ESCALATED)) == ()
    await warden_end.close()


async def test_a_critical_request_is_shown_once_by_the_queens_decision() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(clock, guard=GuardDeps(pause_timeout_s=0.0))
    queen = Queen(deps)
    report = make_guard_report(clock, cell_id=link.cell.id, confidence=GuardConfidence.CRITICAL)

    await queen.report_to_human(report)  # Filed, not shown: nothing waits on the human yet.
    assert queen.human_inbox.alarms == ()
    await queen.file_guard_request(report)  # The Guard Bee files it too: still one request.
    [item] = await guard_items(deps)
    site = isolation_site(deps, link)
    await decide_guard_item(site, item)  # She isolates, and the human hears of it once.
    await queen.report_to_human(report)  # A late repeat shows nothing more.

    alarm_id = report_alarm_id(report.id)
    shown = await deps.trail.query(TrailQuery(kind=_ESCALATED, subject_id=alarm_id))
    assert len(shown) == 1
    assert [alarm.id for alarm in site.human_inbox.alarms] == [alarm_id]
    assert queen.human_inbox.alarms == ()
    await warden_end.close()


def test_every_showing_of_a_report_carries_its_own_alarm_id() -> None:
    report = make_guard_report()

    alarm_id = report_alarm_id(report.id)

    assert alarm_id.startswith("alarm_") and alarm_id.endswith(report.id.split("_", 1)[1])
    assert report_alarm_id(report.id) == alarm_id
