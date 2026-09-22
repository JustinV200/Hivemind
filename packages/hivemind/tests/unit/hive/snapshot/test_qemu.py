"""Unit tests for hivemind.hive.snapshot.qemu: QemuSnapshotter over FakeQemuRunner.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/snapshot/qemu.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.snapshot.qemu for QemuSnapshotter, the class under test.
    - packages/hivemind/tests/contracts/test_snapshotter_contract.py for the shared contract
      suite this module's own fixture also feeds.
"""

from __future__ import annotations

import pytest
from builders.cells import make_cell

from hivemind.cell import Cell, CellKind, SnapshotId
from hivemind.hive.backends.qemu.fake import FakeQemuRunner
from hivemind.hive.backends.qemu.runner import QemuRunnerError
from hivemind.hive.snapshot.ledger import SnapshotLedger, SnapshotNotFoundError
from hivemind.hive.snapshot.qemu import QemuSnapshotter
from waggle.clock import FakeClock


def _virtual_cell(clock: FakeClock) -> Cell:
    return make_cell(kind=CellKind.VIRTUAL, clock=clock)


async def test_snapshot_calls_savevm_for_the_cells_own_vm() -> None:
    clock = FakeClock()
    runner = FakeQemuRunner()
    cell = _virtual_cell(clock)
    snapshotter = QemuSnapshotter(runner, SnapshotLedger(), clock)

    await snapshotter.snapshot(cell)

    assert len(runner.savevm_calls) == 1
    assert runner.savevm_calls[0][0] == cell.id


async def test_snapshot_then_rollback_loads_the_matching_tag() -> None:
    clock = FakeClock()
    runner = FakeQemuRunner()
    cell = _virtual_cell(clock)
    snapshotter = QemuSnapshotter(runner, SnapshotLedger(), clock)

    first_id = await snapshotter.snapshot(cell)
    second_id = await snapshotter.snapshot(cell)
    await snapshotter.rollback(cell, first_id)

    assert first_id != second_id
    assert len(runner.loadvm_calls) == 1
    loaded_cell_id, loaded_tag = runner.loadvm_calls[0]
    assert loaded_cell_id == cell.id
    # The tag rollback loaded matches the *first* snapshot's own savevm call, not the second one.
    assert loaded_tag == runner.savevm_calls[0][1]


async def test_rollback_of_an_unknown_snapshot_id_raises() -> None:
    clock = FakeClock()
    snapshotter = QemuSnapshotter(FakeQemuRunner(), SnapshotLedger(), clock)
    cell = _virtual_cell(clock)

    with pytest.raises(SnapshotNotFoundError):
        await snapshotter.rollback(cell, SnapshotId("snap_qemu_never-taken"))


async def test_underlying_loadvm_failure_for_a_tag_the_fake_never_saved_still_raises() -> None:
    """FakeQemuRunner.loadvm itself raises for a tag it never tracked (mirrors real QEMU)."""
    runner = FakeQemuRunner()
    cell = _virtual_cell(FakeClock())

    with pytest.raises(QemuRunnerError):
        await runner.loadvm(cell.id, "never-saved")


async def test_snapshot_records_disk_usage_on_the_shared_ledger() -> None:
    clock = FakeClock()
    runner = FakeQemuRunner()
    runner.set_savevm_size_bytes(2048)
    cell = _virtual_cell(clock)
    ledger = SnapshotLedger()
    snapshotter = QemuSnapshotter(runner, ledger, clock)

    await snapshotter.snapshot(cell)

    assert ledger.disk_used_bytes(cell.id) == 2048


async def test_snapshot_over_budget_evicts_the_oldest_snapshot_first() -> None:
    clock = FakeClock()
    runner = FakeQemuRunner()
    runner.set_savevm_size_bytes(600)
    cell = _virtual_cell(clock)
    ledger = SnapshotLedger()
    snapshotter = QemuSnapshotter(runner, ledger, clock, disk_budget_bytes=1000)

    first_id = await snapshotter.snapshot(cell)
    clock.advance(1.0)
    second_id = await snapshotter.snapshot(cell)  # 600 + 600 > 1000: evicts `first_id`.

    with pytest.raises(SnapshotNotFoundError):
        ledger.get(first_id)
    assert ledger.get(second_id).cell_id == cell.id
    assert ledger.disk_used_bytes(cell.id) == 600
