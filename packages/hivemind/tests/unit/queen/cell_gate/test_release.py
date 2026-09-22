"""Unit tests for hivemind.queen.cell_gate.release: make_on_task_finished.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/cell_gate/release.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.release for make_on_task_finished, the function under test.
"""

from __future__ import annotations

from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.brood_chamber import TaskOutcome, TaskStatus
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.lifecycle import CellLifecycle, OverwinterSettings
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import OverwinterConfig
from hivemind.hive.overwinter.pool import OverwinterPool
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.release import make_on_cell_granted, make_on_task_finished
from waggle.clock import FakeClock
from waggle.ids import CellId, new_grant_id, new_hive_id, new_warden_id


async def _no_op_scrub(cell: object) -> None:
    """A Scrubber that does nothing."""


def _spec(**overrides: object) -> VirtualCellSpec:
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 1.0,
        "memory_bytes": 1024**3,
        "disk_bytes": 8 * 1024**3,
        "capacity": make_capacity(),
        "hive_id": new_hive_id(FakeClock()),
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _outcome(status: TaskStatus) -> TaskOutcome:
    verified_by = new_warden_id(FakeClock()) if status is TaskStatus.SUCCEEDED else None
    return TaskOutcome(
        status=status, summary="done", artifacts=(), verified_by=verified_by, spend_usd=0.0
    )


def _build_lifecycle(*, with_pool: bool) -> tuple[CellLifecycle, FakeCellBackend]:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = BackendRegistry()
    backend = FakeCellBackend(
        clock, capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None)
    )
    registry.register("fake", lambda: backend)
    config = OverwinterConfig(
        enabled=True, max_cells=10, max_per_image=10, max_dormant_s=3600.0, disk_budget_mb=1024**3
    )
    overwinter = (
        OverwinterSettings(pool=OverwinterPool(clock, config), config=config) if with_pool else None
    )
    lifecycle = CellLifecycle(registry, trail, clock, make_identity(clock), overwinter=overwinter)
    return lifecycle, backend


async def _granted_cell(lifecycle: CellLifecycle) -> CellId:
    """Provision, mark ready and grant a fresh Cell; return its id, still GRANTED."""
    cell = await lifecycle.provision(_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    return cell.id


async def test_on_task_finished_is_a_no_op_for_a_cell_the_lifecycle_does_not_track() -> None:
    lifecycle, _backend = _build_lifecycle(with_pool=True)
    on_task_finished = make_on_task_finished(lifecycle, _no_op_scrub)

    # A real Cell id, or any id this lifecycle never provisioned: must not raise.
    await on_task_finished(CellId("cell_not_tracked"), _outcome(TaskStatus.SUCCEEDED))


async def test_on_task_finished_overwinters_a_succeeded_task_when_the_pool_accepts_it() -> None:
    lifecycle, backend = _build_lifecycle(with_pool=True)
    cell_id = await _granted_cell(lifecycle)
    on_task_finished = make_on_task_finished(lifecycle, _no_op_scrub)

    await on_task_finished(cell_id, _outcome(TaskStatus.SUCCEEDED))

    assert lifecycle.status_of(cell_id) is VirtualCellStatus.DORMANT
    assert backend.pause_calls == [cell_id]


async def test_on_task_finished_tears_down_without_a_pool() -> None:
    lifecycle, backend = _build_lifecycle(with_pool=False)
    cell_id = await _granted_cell(lifecycle)
    on_task_finished = make_on_task_finished(lifecycle, _no_op_scrub)

    await on_task_finished(cell_id, _outcome(TaskStatus.SUCCEEDED))

    assert lifecycle.status_of(cell_id) is None
    assert backend.destroy_calls == [cell_id]


async def test_on_task_finished_tears_down_a_failed_task_without_ever_asking_the_pool() -> None:
    lifecycle, backend = _build_lifecycle(with_pool=True)
    cell_id = await _granted_cell(lifecycle)
    on_task_finished = make_on_task_finished(lifecycle, _no_op_scrub)

    await on_task_finished(cell_id, _outcome(TaskStatus.FAILED))

    assert lifecycle.status_of(cell_id) is None
    assert backend.destroy_calls == [cell_id]
    assert backend.pause_calls == []  # Never offered to decide_release at all.


async def test_on_cell_granted_walks_ready_to_granted_once_and_ignores_a_regrant() -> None:
    """A retry or redispatch grants an already-GRANTED Cell again; that must not raise."""
    lifecycle, _backend = _build_lifecycle(with_pool=False)
    cell = await lifecycle.provision(_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    on_cell_granted = make_on_cell_granted(lifecycle)

    await on_cell_granted(cell.id, new_grant_id(FakeClock()))
    assert lifecycle.status_of(cell.id) is VirtualCellStatus.GRANTED
    await on_cell_granted(cell.id, new_grant_id(FakeClock()))  # A re-grant: no edge, no error.
    assert lifecycle.status_of(cell.id) is VirtualCellStatus.GRANTED
    await on_cell_granted(CellId("cell_untracked"), new_grant_id(FakeClock()))  # Real Cell: no-op.
