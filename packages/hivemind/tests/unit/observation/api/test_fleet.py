"""Test hivemind.observation.api.fleet: the Fleet list's rows, badges and fixed legend.

An empty Hive has no rows but always the three-tier legend. A populated one shows each Cell's
headline status, the Warden's place, the models badge, the current task and its role, open Alarms,
the highest live Cell Wax severity (an expired note does not count), the lease age and the ids the
row links through.

Fits into the Hive:
    Mirrors src/hivemind/observation/api/fleet.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

from datetime import timedelta

from builders.forage import make_source
from builders.memory import make_cell_wax
from builders.observation import after, make_cell_view, make_event, make_sample, make_warden_view
from builders.tasks import make_task, make_task_spec
from pydantic import JsonValue

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import HIVE_STAND_SOURCE, CellKind, CombShieldLevel, LeaseState
from hivemind.forage import HostingPlan, SourceChain
from hivemind.hive import VirtualCellStatus
from hivemind.memory import WaxSeverity, WaxState
from hivemind.observation import (
    TIER_LEGEND,
    FleetInputs,
    OpenAlarmView,
    cell_status,
    fleet_view,
    highest_severity,
    hosting_facts,
    live_wax,
    open_alarms,
    warden_runs,
)
from waggle.clock import FakeClock
from waggle.ids import new_alarm_id, new_worker_id
from waggle.messages.supervision import WardenState, WorkerState
from waggle.messages.task import WorkerRole

_CLOCK = FakeClock()
_NOW = after(600)


def _inputs(**fields: object) -> FleetInputs:
    """Inputs for an otherwise empty Hive, with ``fields`` replacing the empty defaults."""
    base: dict[str, object] = {
        "cells": [],
        "wardens": [],
        "tasks": [],
        "alarms": (),
        "wax": [],
        "hosting": hosting_facts([], [], []),
        "samples": [],
        "lease_since": {},
        "as_of": _NOW,
    }
    base.update(fields)
    return FleetInputs(**base)  # type: ignore[arg-type]


def test_an_empty_hive_has_no_rows_but_always_the_three_tier_legend() -> None:
    view = fleet_view(_inputs())

    assert view.rows == []
    assert [entry.tier for entry in view.legend] == [
        CombShieldLevel.MEADOW,
        CombShieldLevel.PROPOLIS,
        CombShieldLevel.NIGHT_VEIL,
    ]
    assert view.legend == list(TIER_LEGEND)
    assert all(entry.meaning for entry in view.legend)


def _open_alarm_on(warden_id: str, cell_id: str) -> tuple[OpenAlarmView, ...]:
    """One open Alarm recorded by a Warden, placed on its Cell."""
    payload: dict[str, JsonValue] = {
        "kind": "WORKER_FAILED",
        "severity": "WARNING",
        "task_id": None,
    }
    event = make_event(
        "alarm.raised", new_alarm_id(_CLOCK), after(1), actor=warden_id, payload=payload
    )
    return open_alarms([event], {}, {warden_id: cell_id})


def test_a_row_joins_the_cells_warden_work_alarms_wax_lease_and_hosting() -> None:
    warden = make_warden_view(state=WardenState.ACTIVE)
    sub_bee = new_worker_id(_CLOCK)
    task = make_task(TaskStatus.RUNNING, _CLOCK, spec=make_task_spec(role=WorkerRole.FORAGER))
    cell = make_cell_view(
        id=warden.cell_id,
        warden_id=warden.id,
        source=HIVE_STAND_SOURCE,
        comb_shield=CombShieldLevel.PROPOLIS,
        lease_state=LeaseState.OPEN,
        current_tasks=[task.id],
    )
    alarms = _open_alarm_on(warden.id, warden.cell_id)
    expired = make_cell_wax(
        _CLOCK, cell_id=cell.id, severity=WaxSeverity.BLOCK, expires_at=after(100)
    )
    live = make_cell_wax(_CLOCK, cell_id=cell.id, severity=WaxSeverity.CAUTION)
    plan = HostingPlan(cell_id=cell.id, reason="test", default=SourceChain(primary="mine"))
    hosting = hosting_facts(
        [plan], [make_source(source_id="mine", host_cell_id=cell.id)], [cell.id]
    )

    view = fleet_view(
        _inputs(
            cells=[cell],
            wardens=[warden],
            tasks=[task],
            alarms=alarms,
            wax=[expired, live],
            hosting=hosting,
            lease_since={cell.id: after(100)},
            samples=[
                make_sample(warden.id),
                make_sample(warden.id, worker_id=sub_bee, worker_state=WorkerState.RUNNING),
            ],
        )
    )

    [row] = view.rows
    assert (row.id, row.status, row.comb_shield) == (cell.id, "ACTIVE", CombShieldLevel.PROPOLIS)
    assert (row.warden_runs, row.models_from) == ("hive_stand", "local")
    assert (row.current_task_id, row.current_task_role) == (task.id, WorkerRole.FORAGER)
    assert (row.open_alarms, row.highest_wax_severity) == (1, WaxSeverity.CAUTION)
    assert row.lease_age_s == 500.0
    assert row.mask_state is None
    assert row.links.attendant_principal == warden.id
    assert row.links.thought_principals == [warden.id, sub_bee]


def test_a_virtual_cells_status_and_warden_place_come_from_its_lifecycle() -> None:
    warden = make_warden_view()
    virtual = make_cell_view(
        kind=CellKind.VIRTUAL, warden_id=warden.id, virtual_status=VirtualCellStatus.GRANTED
    )
    bare = make_cell_view()

    assert cell_status(virtual, warden) == "GRANTED"
    assert warden_runs(virtual) == "on_cell"
    assert cell_status(bare, None) == "UNKNOWN"
    assert warden_runs(bare) is None  # No Warden attached.
    swarm = make_cell_view(warden_id=warden.id, source="swarm")
    assert warden_runs(swarm) is None  # Runtime level untracked until phase 11.


def test_wax_is_live_only_while_written_and_unexpired() -> None:
    live = make_cell_wax(_CLOCK, severity=WaxSeverity.NOTE)
    lapsed = make_cell_wax(_CLOCK, expires_at=_NOW - timedelta(seconds=1))
    cleared = make_cell_wax(_CLOCK, state=WaxState.CLEARED)

    kept = live_wax([live, lapsed, cleared], _NOW)

    assert kept == (live,)
    assert highest_severity(kept) is WaxSeverity.NOTE
    assert highest_severity([]) is None
