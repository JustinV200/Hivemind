"""Test hivemind.entrance.reads.screens.fleet: the Fleet list read from a small Hive.

One Real Cell on the Hive Stand's source with an attached Warden: its row shows the Warden's state
and place, the task placed on it and the role working it, an open Alarm placed through the task, the
highest written Cell Wax severity, the lease age and hosting from the plan and the Forage map; the
tier legend is there however many Cells there are.

Fits into the Hive:
    Mirrors src/hivemind/entrance/reads/screens/fleet.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from builders.cells import make_cell
from builders.entrance.screens import StaticMap, heartbeat, scene
from builders.forage import make_source
from builders.memory import make_cell_wax
from builders.observation import make_event

from hivemind.cell import HIVE_STAND_SOURCE, CellKind, CombShieldLevel
from hivemind.entrance.reads.screens import read_fleet
from hivemind.forage import HostingPlan, SourceChain
from hivemind.memory import WaxSeverity
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id
from waggle.messages.task import WorkerRole


async def test_a_cells_row_joins_its_warden_work_alarm_wax_lease_and_hosting() -> None:
    clock = FakeClock()
    cell = make_cell(
        CellKind.REAL, clock, source=HIVE_STAND_SOURCE, comb_shield=CombShieldLevel.PROPOLIS
    )
    hive = scene(
        clock,
        cell=cell,
        forage_map=StaticMap((make_source(source_id="mine", host_cell_id=cell.id),)),
    )
    [task] = await hive.submit({"a": ()})
    await hive.place(task)
    hive.board.record(hive.link.warden_id, heartbeat(), clock.now())
    await hive.deps.ledger.decisions.record_plan(
        HostingPlan(cell_id=cell.id, reason="test", default=SourceChain(primary="mine"))
    )
    await hive.write_wax(make_cell_wax(clock, cell_id=cell.id, severity=WaxSeverity.BLOCK))
    payload = {"kind": "WORKER_FAILED", "severity": "WARNING", "task_id": task.id}
    await hive.record(make_event("alarm.raised", new_alarm_id(clock), clock.now(), payload=payload))
    await hive.record(make_event("cell.leased", cell.id, clock.now()))
    clock.advance(30)

    view = await read_fleet(hive.reads, clock.now())

    [row] = view.rows
    assert (row.id, row.status, row.comb_shield) == (cell.id, "ACTIVE", CombShieldLevel.PROPOLIS)
    assert (row.warden_runs, row.models_from, row.warden_id) == (
        "hive_stand",
        "local",
        hive.link.warden_id,
    )
    assert (row.current_task_id, row.current_task_role) == (task.id, WorkerRole.DRONE)
    assert (row.open_alarms, row.highest_wax_severity) == (1, WaxSeverity.BLOCK)
    assert row.lease_age_s == 30.0  # The open lease began 30 seconds before the read.
    assert row.links.thought_principals == [hive.link.warden_id]
    assert len(view.legend) == 3


async def test_the_legend_is_present_whatever_the_fleet() -> None:
    clock = FakeClock()
    hive = scene(clock)

    view = await read_fleet(hive.reads, clock.now())

    assert [row.status for row in view.rows] == ["UNKNOWN"]
    assert [entry.tier for entry in view.legend] == list(CombShieldLevel)
