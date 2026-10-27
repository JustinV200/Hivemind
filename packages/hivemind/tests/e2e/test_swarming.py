"""End-to-end: a goal with many subtasks swarms up to max_parallel Cells, and back to zero.

Roadmap step 13.3 (ADR-0063) through a whole Hive: the Queen, `CellListener`, the lifecycle and a
real in-Cell Warden per Virtual Cell over `builders.virtual_cells.ContainerSpawningFakeCellBackend`,
as `tests.e2e.test_virtual_cells` builds it. Six independent tasks, `prefer = "virtual"`, the Hive
Stand excluded and `[virtual_cells.swarming] max_parallel = 2`. The fleet must reach two Cells and
never pass them; a task with no Cell to take waits with `queen.decided` naming the cap; every task
finishes, sharing the two Cells as their sub-bee room allows, and with Overwintering off the fleet
is back to zero before the Hive stops.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.scheduler.swarming for the policy.
    - hivemind.queen.dispatcher.snapshot for where it holds a backend back.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.virtual_cells import (
    DEFAULT_HAIKU_FILES,
    ContainerSpawningFakeCellBackend,
    VirtualCellsTuning,
    independent_haiku_plan,
    virtual_cells_manifest,
)
from e2e.kernel_helpers import HaikuScript, default_worker_turn, wait_until

from hivemind.cell import HoneyClearance
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.manifest import load_manifest
from hivemind.pheromone import TrailQuery
from waggle.clock import SystemClock

pytestmark = pytest.mark.e2e

_MAX_PARALLEL = 2
_TASKS = 6  # Three times the cap: the goal must queue behind it.
_TIMEOUT_S = 30.0
_SAMPLE_S = 0.005  # Fast enough to see a Cell that lives for one short task.


def test_a_goal_swarms_up_to_max_parallel_and_back_to_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Six tasks under max_parallel 2: two Cells at peak, never three, none at the end."""
    monkeypatch.setattr(
        "hivemind.cli.compose.virtual_cell_backends.FakeCellBackend",
        ContainerSpawningFakeCellBackend,
    )
    tuning = VirtualCellsTuning(prefer="virtual", allow_hive_stand=False, overwinter_enabled=False)
    manifest_path = virtual_cells_manifest(tmp_path, tuning=tuning)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[virtual_cells.swarming]\nmax_parallel = {_MAX_PARALLEL}\n")
    # Every container writes every default file, so each task's FILE_EXISTS holds wherever it runs.
    plan = independent_haiku_plan(filenames=DEFAULT_HAIKU_FILES * (_TASKS // 3))
    script = HaikuScript(default_worker_turn, plan=plan)
    hive = build_hive(
        load_manifest(manifest_path, {}),
        environ={},
        clock=SystemClock(),
        responders={"fake": script.responder},
    )
    asyncio.run(_run_swarm(hive))


async def _run_swarm(hive: Hive) -> None:
    """Run the goal while sampling the fleet; assert the peak, the cap's reason and the end."""
    assert hive.virtual_cells is not None
    lifecycle = hive.virtual_cells.lifecycle
    backend = hive.virtual_cells.registry.get("fake")
    assert isinstance(backend, ContainerSpawningFakeCellBackend)
    sizes: list[int] = []
    sampler = asyncio.ensure_future(_sample(lifecycle, sizes))
    try:
        async with run_hive(hive):
            report = await run_goal(
                hive,
                "write six haiku about bees",
                clearance=HoneyClearance.C1,
                timeout_s=_TIMEOUT_S,
            )
            assert report.succeeded, report
            assert len(report.tasks) == _TASKS
            # Scale-down: every Cell is torn down once its tasks end, before the Hive stops.
            await wait_until(lambda: not lifecycle.live_cells(), timeout_s=_TIMEOUT_S)
        events = await hive.stores.trail.query(TrailQuery())
    finally:
        sampler.cancel()
        await backend.aclose()
    assert max(sizes) == _MAX_PARALLEL
    assert len(backend.provision_calls) >= _MAX_PARALLEL
    # A task queued behind the cap was told why, once, by the policy's own reason.
    capped = f"swarming: max_parallel {_MAX_PARALLEL} reached"
    assert any(
        e.kind == "queen.decided" and capped in str(e.payload.get("detail", "")) for e in events
    )


async def _sample(lifecycle: CellLifecycle, sizes: list[int]) -> None:
    """Record how many Virtual Cells the lifecycle holds, every few milliseconds."""
    while True:
        sizes.append(len(lifecycle.live_cells()))
        await asyncio.sleep(_SAMPLE_S)
