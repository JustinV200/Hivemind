"""End-to-end: the haiku goal placed in a Virtual Cell under `offline.toml`, still all local.

Phase 8's first exit criterion on the other kind of Cell. With `[placement] prefer = "virtual"` and
the fake backend running a real in-Cell Warden per Cell (`tests/e2e/offline/cells.py`), the haiku
goal is placed in a Virtual Cell, not on the Hive Stand. The Cell's Warden builds its own provider
registry from the table the Queen sent it, so the Drone's calls are made by the Cell's own Fanner
on a real socket to the scripted server (loopback is kept: the fake backend has no gateway host).
Asserted: the goal succeeds; the Drone's `worker.spawned` event comes from another node than the
Queen's; every `llm.call`, the Drone's included, names a loopback provider; no remote connect.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - The Cell's Warden is a real `run_in_cell_warden` task over a real loopback WebSocket.

See Also:
    - tests/e2e/test_virtual_cells.py for the container-spawning pattern.
    - tests/e2e/offline/cells.py for the one change this suite makes to it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.virtual_cells import patch_submit_goal_dispatch_race
from e2e.offline import (
    GOAL,
    HAIKU_FILES,
    EgressLog,
    assert_every_call_local,
    calls_on,
    offline_manifest,
)
from e2e.offline.cells import use_provider_table_cells
from e2e.offline.manifest import HIVE_STAND_CORES
from e2e.offline.rig import TIMEOUT_S
from e2e.scripted_openai import Scenario, serve_in_thread

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import build_hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.pheromone import MAX_QUERY_LIMIT, PheromoneEvent, TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_CELL_MEMORY_BYTES = (
    16 * 1024**3
)  # Room for the Drone's 512 MiB footprint, the reserve and headroom.
# Place the one task on a Virtual Cell the fake backend provisions. Its cores are pinned high for
# the reason the Hive Stand's are (HIVE_STAND_CORES): a Cell's free cores are its cores less the
# one-minute load average of the machine the test runs on, and the in-process "container" reads it.
_VIRTUAL = {
    "placement": {"prefer": "virtual", "allow_hive_stand": True},
    "virtual_cells": {
        "backend": "fake",
        "max_cells": 4,
        "cpu_cores": HIVE_STAND_CORES,
        "memory_bytes": _CELL_MEMORY_BYTES,
        "overwinter": {"enabled": False},
    },
}


def test_the_haiku_goal_runs_in_a_virtual_cell_on_a_loopback_provider(
    tmp_path: Path, egress: EgressLog, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_provider_table_cells(monkeypatch)
    patch_submit_goal_dispatch_race(monkeypatch)
    with serve_in_thread(Scenario(files=HAIKU_FILES)) as base_url:
        manifest, _path = offline_manifest(tmp_path, base_url, _VIRTUAL)
        hive = build_hive(manifest, environ={}, clock=SystemClock())

        async def run() -> tuple[bool, tuple[PheromoneEvent, ...]]:
            async with run_hive(hive):
                report = await run_goal(
                    hive, GOAL, clearance=HoneyClearance.C1, timeout_s=TIMEOUT_S
                )
            events = await hive.stores.trail.query(TrailQuery(limit=MAX_QUERY_LIMIT))
            return report.succeeded, tuple(events)

        succeeded, events = asyncio.run(run())

    assert succeeded
    assert_every_call_local(events, manifest)
    queen_node = str(manifest.hive.node_id)
    [spawned] = [e for e in events if e.kind == "worker.spawned"]
    assert spawned.node_id != queen_node  # The Cell's own Warden spawned the Drone.
    drone_calls = calls_on(events, ModelSlot.WORKER)
    assert drone_calls and {call.node_id for call in drone_calls} == {spawned.node_id}
    egress.assert_clean_and_live()
