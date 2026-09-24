"""End-to-end: a Guard request against a Virtual Cell running a task; the Queen isolates it by rule.

Roadmap step 10.6a on a real run. The Hive is built exactly as phase 10's criterion 1b builds it
(`tests.e2e.test_phase10_exit_criteria`): a real Queen, a real `CellListener`, and a real in-Cell
Warden per Virtual Cell over the container-spawning fake backend. A goal the Hive Stand may not
take lands on a Virtual Cell, whose Drone starts a command that runs on. A Guard report under the
shipped dire pattern is filed through the Queen's door (what the Guard Bee is handed), and on the
tick it wakes she decides it by rule, `queen.decided` first, then carries it out on the one
isolation path: the Cell's BLOCK wax, its Warden's grant revoked, the Drone checkpointed and
paused (its `worker.paused` reaching her trail through the Cell's trail shipping), the task PAUSED,
the Cell's egress cut on the fake backend, `cell.isolated` citing the report, and a CRITICAL
SECURITY Alarm at the human. A second goal submitted afterwards is placed anywhere but that Cell.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.guard_requests.decision and hivemind.queen.isolation for the path.
    - tests.e2e.test_phase10_exit_criteria for the Hive this builds.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from builders.isolation import DIRE_RULE, make_guard_report
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.brood_chamber import TaskFilter, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.guard import GuardReport, GuardReportId
from hivemind.hive import BackendCapabilities
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.fake import ReadinessGateExpect
from hivemind.llm import LLMResponse, ToolCall, text_response, tool_call_response
from hivemind.manifest import load_manifest
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.guard_requests import report_alarm_id
from hivemind.supervision import AlarmKind, AlarmSeverity
from waggle.clock import Clock, SystemClock
from waggle.ids import CellId, TaskId

pytestmark = pytest.mark.e2e

_TIMEOUT_S = 20.0  # Each wait; every step lands in well under a second or two.
_LINGER_S = 30  # The Drone's command outlives the scenario: only the isolation holds it.
# A goal ceiling without `cell:hive_stand`: every task of it lands on a Virtual Cell.
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


def _lingering_script() -> tuple[LLMResponse, ...]:
    """Every container's Drone: start a long command, and only then (never, here) finish."""
    linger = ToolCall(
        id="linger",
        name="run_command",
        arguments={"argv": [sys.executable, "-c", f"import time; time.sleep({_LINGER_S})"]},
    )
    return (tool_call_response(linger), text_response("Done."))


class _LingeringCells(ContainerSpawningFakeCellBackend):
    """The container-spawning fake backend, every container scripted to linger."""

    def __init__(
        self,
        clock: Clock,
        capabilities: BackendCapabilities | None = None,
        *,
        endpoint: QueenEndpoint | None = None,
        gate: ReadinessGateExpect | None = None,
    ) -> None:
        super().__init__(
            clock, capabilities, endpoint=endpoint, gate=gate, script=_lingering_script
        )


def test_a_guard_request_isolates_a_virtual_cell_running_a_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend", _LingeringCells
    )
    # The in-process "containers" probe this very machine (hivemind.cell.local.probe), which the
    # suite shares with other work: a one-minute load above half its cores leaves a Drone's grant
    # at int(1 x 0.9) = 0 sub-bees, a capacity question this scenario is not about. An idle host.
    monkeypatch.setattr(os, "getloadavg", lambda: (0.0, 0.0, 0.0))
    manifest_path = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="real"))
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    asyncio.run(_scenario(hive))


async def _scenario(hive: Hive) -> None:
    """Run the Hive, isolate the busy Virtual Cell on a Guard request, and read what it did."""
    backend = _backend(hive)
    try:
        async with run_hive(hive):
            task_id, cell_id = await _a_busy_virtual_cell(hive, backend)
            report = await _file_request(hive, task_id, cell_id)
            await wait_until(lambda: _decided(hive, report.id), timeout_s=_TIMEOUT_S)
            await _assert_isolated(hive, backend, report.id, task_id, cell_id)
            await _assert_no_further_placement(hive, cell_id)
    finally:
        await backend.aclose()


def _backend(hive: Hive) -> _LingeringCells:
    """The running Hive's own container-spawning fake backend."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, _LingeringCells)
    return backend


async def _a_busy_virtual_cell(hive: Hive, backend: _LingeringCells) -> tuple[TaskId, CellId]:
    """Submit a Virtual-only goal and wait until its Drone is at work in its Cell."""
    goal_id = await hive.queen.submit_goal(
        "write one haiku about bees", clearance=HoneyClearance.C1, capabilities=_VIRTUAL_ONLY
    )
    await wait_until(lambda: bool(backend.providers), timeout_s=_TIMEOUT_S)
    [cell_id] = backend.providers
    # The Drone's first call answered: it is running its command now, inside the Cell.
    await wait_until(lambda: bool(backend.providers[cell_id].calls), timeout_s=_TIMEOUT_S)
    [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    assert task.status is TaskStatus.RUNNING and task.cell_id == cell_id
    return task.id, cell_id


async def _file_request(hive: Hive, task_id: TaskId, cell_id: CellId) -> GuardReport:
    """File a dire-pattern report about the Cell, citing the task's own placement as evidence."""
    [assigned] = await _events(hive, "queen.assigned", task_id)
    report = make_guard_report(
        hive.clock, cell_id=cell_id, rule=DIRE_RULE, event_ids=(assigned.id,), task_ids=(task_id,)
    )
    await hive.guard_door.file_guard_request(report)  # Durable, and wakes her.
    return report


async def _assert_isolated(
    hive: Hive, backend: _LingeringCells, report_id: GuardReportId, task_id: TaskId, cell_id: CellId
) -> None:
    """Every step of the one path, in order, citing the report."""
    [decided] = [
        event
        for event in await _events(hive, "queen.decided", cell_id)
        if event.payload.get("report_id") == report_id
    ]
    assert (decided.payload["action"], decided.payload["basis"]) == ("ISOLATE_CELL", "rule")
    [isolated] = await _events(hive, "cell.isolated", cell_id)
    assert (decided.at, decided.id) <= (isolated.at, isolated.id)
    assert isolated.payload["report_id"] == report_id
    assert isolated.payload["decision_event_id"] == decided.id
    assert isolated.payload["paused_task_ids"] == [task_id]
    assert isolated.payload["unacknowledged_task_ids"] == []  # Its bee answered in time.
    assert isolated.payload["egress"] == "cut" and backend.egress_is_cut(cell_id)
    assert isolated.payload["revoked_grant_ids"]
    assert (await hive.stores.chamber.get(task_id)).status is TaskStatus.PAUSED
    assert await _events(hive, "worker.paused")  # Shipped from inside the Cell to her trail.
    [alarm] = [a for a in hive.queen.human_inbox.alarms if a.kind is AlarmKind.SECURITY]
    assert alarm.id == report_alarm_id(report_id) and alarm.severity is AlarmSeverity.CRITICAL


async def _assert_no_further_placement(hive: Hive, cell_id: str) -> None:
    """A second goal is placed anywhere but the isolated Cell."""
    goal_id = await hive.queen.submit_goal(
        "write another haiku", clearance=HoneyClearance.C1, capabilities=_VIRTUAL_ONLY
    )
    await wait_until(lambda: _placed(hive, goal_id), timeout_s=_TIMEOUT_S)
    [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    [assigned] = await _events(hive, "queen.assigned", task.id)
    assert assigned.payload["cell_id"] != cell_id
    # Let its own Cell finish coming up before the Hive shuts down around it.
    backend = _backend(hive)
    other = CellId(str(assigned.payload["cell_id"]))
    await wait_until(lambda: bool(backend.providers.get(other)), timeout_s=_TIMEOUT_S)
    await wait_until(lambda: bool(backend.providers[other].calls), timeout_s=_TIMEOUT_S)


async def _placed(hive: Hive, goal_id: TaskId) -> bool:
    """Whether the goal's one task has been assigned somewhere."""
    [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    return bool(await _events(hive, "queen.assigned", task.id))


async def _decided(hive: Hive, report_id: GuardReportId) -> bool:
    """Whether the Queen has stamped her decision on the request: every step of it is done."""
    row = await hive.queen._deps.guard.requests.get(report_id)
    return row is not None and row.decision is not None


async def _events(hive: Hive, kind: str, subject_id: str | None = None) -> list[PheromoneEvent]:
    """Every `kind` event (about `subject_id`, when given) on the Queen's trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind, subject_id=subject_id)))
