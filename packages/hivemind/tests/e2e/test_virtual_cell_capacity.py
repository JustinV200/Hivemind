"""End-to-end: a Virtual Cell's capacity is its reservation, however busy the host it runs on.

A container shares its host's kernel, so a Virtual Cell that probed itself read the host's cores,
memory and load average: on a Hive Stand loaded to about 3 on 4 cores every Virtual Cell looked
just as loaded, its grant came to zero sub-bees, and its task failed. This module fakes that busy
host for the whole process (`os.getloadavg` at 3.9, which the Hive Stand's probe and the in-Cell
Warden of `builders.virtual_cells.ContainerSpawningFakeCellBackend` both read, as a real container
would) and runs one goal onto a Virtual Cell through a whole Hive: the Cell must report the
reservation its bootstrap named, the same figures the Queen placed it by, and its task must get
its bee and succeed.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.models for CellReservation, the capacity both sides compute.
    - hivemind.cli.in_cell.config for the Cell's own reading of its reservation.
    - tests.e2e.test_slow_provision for the same Hive built around a slow provision.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from builders.virtual_cells import (
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    single_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.brood_chamber import TaskFilter, TaskStatus, is_terminal
from hivemind.cell import CellKind, HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_hive
from hivemind.hive import CellReservation
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from waggle.clock import SystemClock
from waggle.ids import TaskId

pytestmark = pytest.mark.e2e

_BUSY = (3.9, 3.9, 3.9)  # About this loaded, a four-core Hive Stand left its Cells no free core.
_TIMEOUT_S = 30.0
_MAX_SUB_BEES = 4  # hivemind.cli.compose.virtual_cells' own Virtual Cell cap.


def test_a_virtual_cell_on_a_busy_host_reports_its_reservation_and_gets_its_bee(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole process reads a busy host; the Virtual Cell's goal still succeeds."""
    monkeypatch.setattr(os, "getloadavg", lambda: _BUSY, raising=False)  # Absent on Windows.
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    # Overwintering keeps the Cell, and its link, attached once its task is done.
    manifest_path = virtual_cells_manifest(tmp_path, tuning=VirtualCellsTuning(prefer="virtual"))
    script = HaikuScript(default_worker_turn, plan=single_haiku_plan("haiku_1.txt"))
    hive = build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    asyncio.run(_run_on_a_busy_host(hive))


async def _run_on_a_busy_host(hive: Hive) -> None:
    """Run one goal onto a Virtual Cell, then compare the Cell's reported capacity to its spec."""
    assert hive.virtual_cells is not None
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    section = hive.manifest.virtual_cells
    reserved = CellReservation(
        cpu_cores=section.cpu_cores,
        memory_bytes=section.memory_bytes,
        disk_bytes=section.disk_bytes,
        max_sub_bees=_MAX_SUB_BEES,
    )
    try:
        async with run_hive(hive):
            goal_id = await hive.queen.submit_goal(
                "write one haiku about bees", clearance=HoneyClearance.C1
            )
            await wait_until(lambda: _goal_finished(hive, goal_id), timeout_s=_TIMEOUT_S)
            [cell] = [link.cell for link in hive.queen.wardens if _is_virtual(link.cell.kind)]
            [task] = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
        # The Cell reported what its spec reserved for it, no load at all, on its own platform.
        expected = reserved.capacity(arch=cell.capacity.host.arch, os=cell.capacity.host.os)
        assert cell.capacity == expected
        assert task.status is TaskStatus.SUCCEEDED
        # Its grant held a bee: the Cell's own figures, not the host's load, sized it.
        granted = await hive.stores.trail.query(TrailQuery(kind="forage.granted"))
        bees = [e.payload["max_sub_bees"] for e in granted if e.payload["task_id"] == task.id]
        assert bees
        assert all(isinstance(count, int) and count >= 1 for count in bees)
    finally:
        await backend.aclose()


def _is_virtual(kind: CellKind) -> bool:
    """True for a Virtual Cell: the attached Warden whose Cell this test provisioned."""
    return kind is CellKind.VIRTUAL


async def _goal_finished(hive: Hive, goal_id: TaskId) -> bool:
    """True once every task under `goal_id` has reached a terminal status."""
    tasks = await hive.stores.chamber.list(TaskFilter(goal_id=goal_id))
    return bool(tasks) and all(is_terminal(task.status) for task in tasks)
