"""Unit tests for hivemind.cell.snapshot: NoopSnapshotter's snapshot/rollback behaviour."""

from __future__ import annotations

import pytest
import structlog
from builders.cells import make_cell

from hivemind.cell.errors import SnapshotUnsupportedError
from hivemind.cell.snapshot import NOOP_SNAPSHOT_ID, NoopSnapshotter, SnapshotId


async def test_snapshot_always_returns_the_fixed_noop_id() -> None:
    snapshotter = NoopSnapshotter()
    cell = make_cell()

    result = await snapshotter.snapshot(cell)

    assert result == SnapshotId(NOOP_SNAPSHOT_ID)


async def test_snapshot_warns_once_per_cell_id() -> None:
    snapshotter = NoopSnapshotter()
    cell = make_cell()

    with structlog.testing.capture_logs() as captured:
        await snapshotter.snapshot(cell)
        await snapshotter.snapshot(cell)

    warnings = [entry for entry in captured if entry["event"] == "snapshot.unsupported"]
    assert len(warnings) == 1


async def test_rollback_always_raises_snapshot_unsupported() -> None:
    snapshotter = NoopSnapshotter()
    cell = make_cell()

    with pytest.raises(SnapshotUnsupportedError):
        await snapshotter.rollback(cell, SnapshotId(NOOP_SNAPSHOT_ID))
