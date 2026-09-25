"""End-to-end: a Drone lured and refused inside a Virtual Cell; the Queen isolates the Cell.

Roadmap steps 10.6 and 10.6a on a real run over the Virtual side. The Hive is composed by
`build_hive` exactly as phase 10's criterion 1b builds it: a real Queen and `CellListener`, and a
real in-Cell Warden per Virtual Cell over the container-spawning fake backend, every container's
Drone scripted with the lure (`builders.guard_bee.lure_script`). A goal the Hive Stand may not
take lands on a Virtual Cell. Inside it the Drone lands outside text in its scratch, reads it back
(flagged by the Cell's own scanner), is refused a host it holds no `net` capability for, and
starts a long command. The flag and the refusal are recorded on the Cell's own trail segment and
reach the Queen's trail as it ships.

The composed Guard Bee reads them there (a segment merged late is read from its node's own
position), correlates them in that one episode, and files one request through the Queen's door.
Its rule is a shipped dire pattern, so she decides it by rule on the tick it wakes and carries it
out on the one isolation path: `queen.decided` first, the Drone paused, the Cell's egress cut on
the fake backend, `cell.isolated` citing the report and her decision, and a CRITICAL SECURITY
Alarm naming the report.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests.e2e.test_guard_bee_on_hive_stand for the same lure on the Hive Stand's own lease.
    - tests.e2e.test_isolation_on_virtual_cell for the isolation path on a filed request alone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.guard_bee import LuredCells, quick_rounds
from builders.virtual_cells import (
    VirtualCellsTuning,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.guard import GuardReportId
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.guard_requests import report_alarm_id
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import SystemClock
from waggle.ids import CellId, TaskId

pytestmark = pytest.mark.e2e

_WAIT_S = 20.0  # Each wait; every step lands within a second or two.
# A goal ceiling without `cell:hive_stand` and without any `net:`: every task of it lands on a
# Virtual Cell, and its Drone holds no host to reach.
_VIRTUAL_ONLY = (
    "cell:virtual",
    "cell:comb_shield:*",
    "llm:*",
    "tool:*",
    "fs:read:**",
    "fs:write:**",
    "exec:*",
    "question:human",
)


def _hive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Hive:
    """Compose the Hive with a Virtual side whose containers run the lured Drone."""
    monkeypatch.setattr("hivemind.cli.compose.virtual_cell_backends.FakeCellBackend", LuredCells)
    manifest_path = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real"))
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    return build_hive(
        load_manifest(quick_rounds(manifest_path), {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )


async def _events(hive: Hive, kind: str) -> list[PheromoneEvent]:
    """Every `kind` event on the Queen's trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


async def _isolated(hive: Hive) -> bool:
    """Whether the Queen has recorded an isolation yet."""
    return bool(await _events(hive, "cell.isolated"))


async def _scenario(hive: Hive) -> TaskId:
    """Run the lured goal on a Virtual Cell until the Queen has isolated it; the task's id."""
    backend = hive.virtual_cells.registry.get("fake") if hive.virtual_cells else None
    assert isinstance(backend, LuredCells)
    try:
        async with run_hive(hive):
            goal_id = await hive.queen.submit_goal(
                "write one haiku", clearance=HoneyClearance.C1, capabilities=_VIRTUAL_ONLY
            )
            await wait_until(lambda: _isolated(hive), timeout_s=_WAIT_S)
            [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
            await _assert_isolated_on_request(hive, backend, task.id)
    finally:
        await backend.aclose()
    return task.id


async def _assert_isolated_on_request(hive: Hive, backend: LuredCells, task_id: TaskId) -> None:
    """The one request, decided by rule, carried out on the one isolation path."""
    stand = hive.manifest.hive.node_id
    [flag] = await _events(hive, "guard.injection_suspected")
    [denied] = [e for e in await _events(hive, "guard.denied") if e.subject_id == flag.subject_id]
    assert flag.node_id == denied.node_id != stand  # Both recorded inside the Cell, shipped.
    # Its link proved every frame and segment it shipped: the Cell gate refused nothing.
    assert await _events(hive, "guard.envelope_refused") == []
    assert await _events(hive, "guard.segment_refused") == []
    [alert] = [a for a in await _events(hive, "guard.alert") if a.payload["disposition"] == "filed"]
    report_id = alert.payload["report_id"]
    [decided] = [
        e for e in await _events(hive, "queen.decided") if e.payload["report_id"] == report_id
    ]
    assert (decided.payload["action"], decided.payload["basis"]) == ("ISOLATE_CELL", "rule")
    [isolated] = await _events(hive, "cell.isolated")
    assert isolated.subject_id == alert.payload["cell_id"] == decided.subject_id
    assert isolated.payload["report_id"] == report_id
    assert isolated.payload["decision_event_id"] == decided.id
    assert isolated.payload["paused_task_ids"] == [task_id]
    assert isolated.payload["egress"] == "cut" and backend.egress_is_cut(
        CellId(isolated.subject_id)
    )
    assert (await hive.stores.chamber.get(task_id)).status is TaskStatus.PAUSED
    [alarm] = [
        a
        for a in hive.queen.human_inbox.alarms
        if a.id == report_alarm_id(GuardReportId(str(report_id)))
    ]
    assert (alarm.kind, alarm.severity) == (AlarmKind.SECURITY, AlarmSeverity.CRITICAL)


def test_a_lured_drone_in_a_virtual_cell_gets_its_cell_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hive = _hive(tmp_path, monkeypatch)

    task_id = asyncio.run(_scenario(hive))

    rows = asyncio.run(hive.queen_deps.guard.requests.pending())
    assert rows == ()  # Her one request is decided; nothing waits.
    assert task_id
