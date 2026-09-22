"""Unit tests for hivemind.queen.cell_gate.snapshot: CellSnapshotHandler.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors src/hivemind/queen/cell_gate/snapshot.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.cell_gate.snapshot for CellSnapshotHandler, the class under test.
    - packages/hivemind/tests/unit/queen/cell_gate/test_release.py for the CellLifecycle-over-
      FakeCellBackend setup this module's own `_build_handler` mirrors.
"""

from __future__ import annotations

from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.registry import BackendRegistry
from hivemind.hive.snapshot import SnapshotLedger
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.cell_gate.snapshot import CellSnapshotHandler
from waggle.clock import FakeClock
from waggle.ids import CellId, new_hive_id
from waggle.messages.cell.snapshot import CellRollbackRequest, CellSnapshotRequest

_UNKNOWN_CELL = CellId("cell_01ARZ3NDEKTSV4RRFFQ69G5FAV")


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


async def _build_handler() -> tuple[CellSnapshotHandler, CellId]:
    """Build a CellSnapshotHandler over one provisioned Cell on a can_snapshot=False backend.

    `FakeCellBackend`'s own default declares `can_snapshot=False`, so `snapshotter_for` always
    hands back `hivemind.cell.NoopSnapshotter` for it: `snapshot()` always succeeds trivially and
    `rollback()` always raises `SnapshotUnsupportedError` (that class's own module docstring) --
    exactly the two outcomes this handler needs to answer, without a real Docker/QEMU daemon.
    """
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    registry = BackendRegistry()
    backend = FakeCellBackend(
        clock, capabilities=BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None)
    )
    registry.register("fake", lambda: backend)
    lifecycle = CellLifecycle(registry, trail, clock, make_identity(clock))
    cell = await lifecycle.provision(_spec(), "fake")
    handler = CellSnapshotHandler(lifecycle, SnapshotLedger(), clock)
    return handler, cell.id


async def test_snapshot_answers_error_for_a_cell_the_lifecycle_does_not_track() -> None:
    handler, _cell_id = await _build_handler()

    reply = await handler.snapshot(CellSnapshotRequest(cell_id=_UNKNOWN_CELL, purpose="test"))

    assert reply.snapshot_id is None
    assert reply.error is not None
    assert "not a Virtual Cell" in reply.error


async def test_snapshot_succeeds_trivially_over_the_noop_snapshotter() -> None:
    handler, cell_id = await _build_handler()

    reply = await handler.snapshot(CellSnapshotRequest(cell_id=cell_id, purpose="test"))

    assert reply.error is None
    assert reply.snapshot_id == "snap_noop"


async def test_rollback_answers_error_for_a_cell_the_lifecycle_does_not_track() -> None:
    handler, _cell_id = await _build_handler()

    reply = await handler.rollback(
        CellRollbackRequest(cell_id=_UNKNOWN_CELL, snapshot_id="snap_noop")
    )

    assert reply.ok is False
    assert reply.error is not None


async def test_rollback_answers_error_over_the_noop_snapshotter() -> None:
    handler, cell_id = await _build_handler()

    reply = await handler.rollback(CellRollbackRequest(cell_id=cell_id, snapshot_id="snap_noop"))

    assert reply.ok is False
    assert reply.error is not None


async def test_the_same_backends_snapshotter_is_reused_across_requests() -> None:
    handler, cell_id = await _build_handler()

    await handler.snapshot(CellSnapshotRequest(cell_id=cell_id, purpose="one"))
    await handler.snapshot(CellSnapshotRequest(cell_id=cell_id, purpose="two"))

    assert len(handler._snapshotters) == 1
