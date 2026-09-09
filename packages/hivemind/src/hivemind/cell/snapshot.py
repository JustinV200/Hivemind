"""Define Snapshotter and NoopSnapshotter: the (usually absent) ability to roll a Cell back.

A `Snapshotter` captures a Cell's state and can roll it back to that point. Virtual Cells (VMs or
containers the Hive provisions) get a real one from their backend in phase 5 (`DockerSnapshotter`,
`QemuSnapshotter`); a Real Cell (an existing device the Hive borrows) has no rollback mechanism the
Hive controls at all -- rolling back somebody else's machine is not a thing a lease can promise
(codingrules section 8.7). `NoopSnapshotter` is what every Real Cell source hands out instead: its
`snapshot` succeeds trivially (there is nothing to capture) and logs a warning once per Cell so the
gap is visible without spamming every call; its `rollback` always raises, because promising to
undo a Real Cell's changes would be a lie.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by NoopSnapshotter here and
    by hive's backend-specific snapshotters (phase 5). Called by `supervision.capping.CappingGate`
    before applying a risky proposal, and by the Undertaker on rollback.
    Calls into hivemind.cell.errors, hivemind.cell.models and hivemind.common.logging only.

Key invariants:
    - NoopSnapshotter.snapshot always returns SnapshotId(NOOP_SNAPSHOT_ID); it never raises.
    - NoopSnapshotter.rollback always raises SnapshotUnsupportedError; it never returns.
    - NoopSnapshotter logs "snapshot.unsupported" at most once per Cell id, not once per call, so
      a Warden that snapshots before every Capping tier does not flood the log.

See Also:
    - .claude/codingrules.md section 8.7 for "Snapshotter protocol... NoopSnapshotter for Real
      Cells with a documented warning."
    - .claude/codingrules.md section 12 for the logging conventions get_logger follows.
    - hivemind.cell.errors for SnapshotUnsupportedError.
"""

from __future__ import annotations

from typing import NewType, Protocol

from hivemind.cell.errors import SnapshotUnsupportedError
from hivemind.cell.models import Cell
from hivemind.common.logging import get_logger

SnapshotId = NewType("SnapshotId", str)

NOOP_SNAPSHOT_ID = "snap_noop"  # The one value NoopSnapshotter.snapshot ever returns.

__all__ = ["NOOP_SNAPSHOT_ID", "NoopSnapshotter", "SnapshotId", "Snapshotter"]

log = get_logger(__name__)


class Snapshotter(Protocol):
    """Capture a Cell's state and be able to roll it back to that point."""

    async def snapshot(self, cell: Cell) -> SnapshotId:
        """Capture `cell`'s current state.

        Args:
            cell: The Cell to snapshot.

        Returns:
            An id `rollback` can later use to restore this point.
        """
        ...

    async def rollback(self, cell: Cell, snapshot: SnapshotId) -> None:
        """Restore `cell` to the state `snapshot` captured.

        Args:
            cell: The Cell to roll back.
            snapshot: An id a prior `snapshot` call on this Cell returned.

        Raises:
            SnapshotUnsupportedError: This Cell has no rollback mechanism.
        """
        ...


class NoopSnapshotter:
    """The Snapshotter every Real Cell source hands out: nothing is captured, nothing rolls back.

    Owns one small piece of mutable state (codingrules section 8.5): which Cell ids it has
    already logged a warning for, so the warning fires once per Cell rather than once per call.
    """

    def __init__(self) -> None:
        """Create a NoopSnapshotter that has warned about nothing yet."""
        self._warned: set[str] = set()

    async def snapshot(self, cell: Cell) -> SnapshotId:
        """Return the fixed no-op snapshot id, warning once per Cell that nothing was captured.

        Args:
            cell: The Cell a caller asked to snapshot.

        Returns:
            SnapshotId(NOOP_SNAPSHOT_ID), always.
        """
        if cell.id not in self._warned:
            log.warning("snapshot.unsupported", cell_id=cell.id)
            self._warned.add(cell.id)
        return SnapshotId(NOOP_SNAPSHOT_ID)

    async def rollback(self, cell: Cell, snapshot: SnapshotId) -> None:
        """Always raise: a Real Cell has no rollback mechanism the Hive controls.

        Args:
            cell: The Cell a caller asked to roll back.
            snapshot: The snapshot id it asked to roll back to; unused.

        Raises:
            SnapshotUnsupportedError: Always.
        """
        raise SnapshotUnsupportedError(cell.id)
