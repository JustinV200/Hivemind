"""Tests for the Hive Stand's exception: only the human isolates it, and the Queen falls back.

Roadmap step 10.6a (ADR-0043). A dire pattern on the Hive Stand's own lease makes the Queen's rule
reach for isolation, and the `isolation` point refuses her there (`guard.denied`, rule
`guard.scope.hive_stand`): nothing is isolated. Instead her decision quarantines the implicated
bee's task through the 10.6c order, holds that goal's new placements off the Hive Stand (a
`PlacementHold` on her decision's row, which placement reads as a block for that goal alone) and
raises a CRITICAL SECURITY Alarm naming the report. The human may still isolate it themselves.

Fits into the Hive:
    Mirrors src/hivemind/queen/guard_requests/decision/hive_stand.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.isolation.authority for the refusal.
"""

from __future__ import annotations

from builders.isolation import (
    hive_stand_cell,
    isolation_site,
    make_guard_report,
    place_running,
)
from builders.queen import make_queen_deps

from hivemind.pheromone import TrailQuery
from hivemind.queen.dispatcher.snapshot import build_inventory
from hivemind.queen.guard_requests import GuardDeps, guard_door, guard_items, report_alarm_id
from hivemind.queen.guard_requests.decision import FALLBACK_OUTCOME, decide_guard_item
from hivemind.queen.isolation import ISOLATED_KIND
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import FakeClock
from waggle.messages.supervision import InterventionAction


async def test_a_dire_pattern_on_the_hive_stand_falls_back_instead_of_isolating() -> None:
    clock = FakeClock()
    deps, link, warden_end = make_queen_deps(
        clock, cell=hive_stand_cell(clock), guard=GuardDeps(pause_timeout_s=0.0)
    )
    task = await place_running(deps, link)
    report = make_guard_report(clock, cell_id=link.cell.id, task_ids=(task.id,))
    site = isolation_site(deps, link)
    await guard_door(deps, site.human_inbox).file_guard_request(report)

    [item] = await guard_items(deps)
    await decide_guard_item(site, item)

    # Human-only: the point refused her, and nothing was isolated.
    [denied] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert (denied.payload["point"], denied.payload["rule"]) == (
        "isolation",
        "guard.scope.hive_stand",
    )
    assert await deps.trail.query(TrailQuery(kind=ISOLATED_KIND)) == ()
    # The fallback: the implicated bee's task is quarantined through its Warden...
    intervene = await warden_end.wait_for_intervene()
    assert (intervene.action, intervene.task_id) == (InterventionAction.QUARANTINE, task.id)
    # ...its goal is held off the Hive Stand, on the decision's own row...
    stamped = await deps.guard.requests.get(report.id)
    assert stamped is not None and stamped.decision is not None and stamped.hold is not None
    assert stamped.decision.outcome == FALLBACK_OUTCOME
    assert (stamped.hold.cell_id, stamped.hold.goal_ids) == (link.cell.id, (task.goal_id,))
    held = await build_inventory(deps, (link,), goal_id=task.goal_id)
    assert link.cell.id in held.blocked
    # ...and the human gets one CRITICAL SECURITY Alarm naming the report.
    [alarm] = site.human_inbox.alarms
    assert alarm.id == report_alarm_id(report.id)
    assert (alarm.kind, alarm.severity) == (AlarmKind.SECURITY, AlarmSeverity.CRITICAL)
    assert report.id in alarm.detail
    await warden_end.close()
