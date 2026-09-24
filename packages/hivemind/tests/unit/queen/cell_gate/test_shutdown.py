"""Tests for hivemind.queen.cell_gate.shutdown: retire every tracked Virtual Cell at shutdown."""

from __future__ import annotations

from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.lifecycle import CellLifecycle, OverwinterSettings
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import OverwinterConfig, ReleaseOutcome
from hivemind.hive.overwinter.pool import OverwinterPool
from hivemind.hive.registry import BackendRegistry
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from hivemind.queen.cell_gate.shutdown import make_retire_all
from waggle.clock import FakeClock
from waggle.ids import CellId, new_grant_id, new_hive_id


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


async def _no_op_scrub(cell: object) -> None:
    """The scrub `lifecycle.overwinter` requires; nothing to remove in these tests."""


def _build_lifecycle() -> tuple[CellLifecycle, FakeCellBackend, MemoryPheromoneTrail]:
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
    overwinter = OverwinterSettings(pool=OverwinterPool(clock, config), config=config)
    lifecycle = CellLifecycle(registry, trail, clock, make_identity(clock), overwinter=overwinter)
    return lifecycle, backend, trail


async def _granted_cell(lifecycle: CellLifecycle) -> CellId:
    cell = await lifecycle.provision(_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    await lifecycle.grant(cell.id, new_grant_id(FakeClock()))
    return cell.id


async def _dormant_cell(lifecycle: CellLifecycle) -> CellId:
    cell_id = await _granted_cell(lifecycle)
    outcome = ReleaseOutcome(
        rolled_back_whole_cell=False, has_block_wax=False, single_use=False, backend_can_pause=True
    )
    await lifecycle.release(cell_id, outcome)
    await lifecycle.overwinter(cell_id, scrub=_no_op_scrub)
    assert lifecycle.status_of(cell_id) is VirtualCellStatus.DORMANT
    return cell_id


class _QuiesceRecorder:
    """Records which Cells were quiesced, in order, standing in for make_quiesce's callable."""

    def __init__(self) -> None:
        self.calls: list[CellId] = []

    async def __call__(self, cell_id: CellId) -> None:
        self.calls.append(cell_id)


async def test_retire_all_with_nothing_tracked_is_a_no_op() -> None:
    lifecycle, backend, _trail = _build_lifecycle()
    retire_all = make_retire_all(lifecycle, _QuiesceRecorder())

    assert await retire_all() == ()
    assert backend.destroy_calls == []


async def test_a_dormant_cell_is_torn_down_without_a_quiesce() -> None:
    """A paused Warden cannot answer a CellTeardownRequest, so none is sent (module docstring)."""
    lifecycle, backend, trail = _build_lifecycle()
    cell_id = await _dormant_cell(lifecycle)
    quiesce = _QuiesceRecorder()
    retire_all = make_retire_all(lifecycle, quiesce)

    retired = await retire_all()

    assert retired == (cell_id,)
    assert quiesce.calls == []
    assert backend.destroy_calls == [cell_id]
    assert lifecycle.status_of(cell_id) is None  # Its record left the table with the Cell.
    kinds = [e.kind for e in await trail.query(TrailQuery(subject_id=cell_id))]
    assert kinds[-2:] == ["cell.destroying", "cell.destroyed"]


async def test_a_granted_cell_is_quiesced_then_torn_down() -> None:
    lifecycle, backend, _trail = _build_lifecycle()
    cell_id = await _granted_cell(lifecycle)
    quiesce = _QuiesceRecorder()
    retire_all = make_retire_all(lifecycle, quiesce)

    retired = await retire_all()

    assert retired == (cell_id,)
    assert quiesce.calls == [cell_id]
    assert backend.destroy_calls == [cell_id]


async def test_a_ready_cell_never_granted_is_quiesced_then_torn_down() -> None:
    lifecycle, backend, _trail = _build_lifecycle()
    cell = await lifecycle.provision(_spec(), "fake")
    await lifecycle.mark_ready(cell.id)
    quiesce = _QuiesceRecorder()
    retire_all = make_retire_all(lifecycle, quiesce)

    assert await retire_all() == (cell.id,)
    assert quiesce.calls == [cell.id]
    assert backend.destroy_calls == [cell.id]


async def test_every_tracked_cell_is_retired_whatever_its_status() -> None:
    lifecycle, backend, _trail = _build_lifecycle()
    dormant = await _dormant_cell(lifecycle)
    granted = await _granted_cell(lifecycle)
    quiesce = _QuiesceRecorder()
    retire_all = make_retire_all(lifecycle, quiesce)

    retired = await retire_all()

    assert set(retired) == {dormant, granted}
    assert quiesce.calls == [granted]  # The dormant one is never quiesced.
    assert set(backend.destroy_calls) == {dormant, granted}
    assert lifecycle.live_cells() == ()


async def test_one_cell_the_backend_refuses_to_destroy_does_not_stop_the_others() -> None:
    """Best-effort per Cell: reconcile and `hive cells abscond` remain the backstops."""
    lifecycle, backend, _trail = _build_lifecycle()
    first = await _granted_cell(lifecycle)
    second = await _granted_cell(lifecycle)
    backend.set_destroy_failure("daemon refused")
    retire_all = make_retire_all(lifecycle, _QuiesceRecorder())

    retired = await retire_all()

    assert retired == ()
    assert backend.destroy_calls == [first, second]  # Both were attempted.
    assert lifecycle.status_of(first) is VirtualCellStatus.DESTROYING
    assert lifecycle.status_of(second) is VirtualCellStatus.DESTROYING


async def test_a_second_call_after_success_finds_nothing_left() -> None:
    lifecycle, backend, _trail = _build_lifecycle()
    await _dormant_cell(lifecycle)
    retire_all = make_retire_all(lifecycle, _QuiesceRecorder())
    await retire_all()

    assert await retire_all() == ()
    assert len(backend.destroy_calls) == 1
