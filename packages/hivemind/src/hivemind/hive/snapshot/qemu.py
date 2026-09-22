"""Define QemuSnapshotter: Snapshotter over QMP `savevm`/`loadvm`, for a QEMU Virtual Cell.

Roadmap step 5.10: "QemuSnapshotter... QMP savevm/loadvm (internal qcow2 snapshots)." `snapshot()`
calls `hivemind.hive.backends.qemu.runner.QemuRunnerPort.savevm` on `cell.id`'s own overlay disk;
`rollback()` calls `loadvm` with the same tag. An internal qcow2 snapshot lives inside the overlay
disk's own file and captures the VM's **full device state** -- memory, CPU registers, every attached
disk -- at the instant it is taken; unlike `hivemind.hive.snapshot.docker.DockerSnapshotter`'s own
filesystem-layer commit, this *does* include in-flight process state, because a qcow2 internal
snapshot is a hypervisor-level checkpoint, not a container-image commit. It still shares Docker's
own caveat about the scratch mount when a QEMU Cell's own scratch lives on a second, separately
attached disk rather than the same overlay (`hivemind.hive.backends.qemu.backend`'s current shape
attaches everything on the one overlay disk, so v0 has no such second disk to worry about; a future
step that splits scratch onto its own volume would need this module's docstring revisited).

Windows QMP limitation: `hivemind.hive.backends.qemu.qmp`'s own module docstring already documents
that QMP over a Unix domain socket does not exist on Windows (`asyncio.open_unix_connection` has no
typeshed stub there); `savevm`/`loadvm` inherit that limitation exactly as `stop_vm`/`pause_vm`/
`resume_vm` already do -- on a Windows Hive Stand running the QEMU backend, every call this module
makes raises `QemuRunnerError` until a TCP QMP endpoint is built (not yet, per that module's "Not
yet built" note).

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.snapshot`. Implements `hivemind.cell.
    Snapshotter`; constructed by the composition root (`hivemind.hive.snapshot.snapshotter_for`)
    and injected into a Warden's `hivemind.supervision.capping.gate.GateDeps.snapshotter`. Calls
    into `hivemind.cell` (Cell, SnapshotId), `hivemind.hive.backends.qemu.runner`
    (QemuRunnerError, QemuRunnerPort), `hivemind.hive.snapshot.ledger` and waggle only.

Key invariants:
    - `capabilities.can_snapshot` is always True for a QEMU backend (ADR-0026), so this class
      never raises `SnapshotUnsupportedError` itself; a genuine QMP failure (including the Windows
      guard above) propagates as `QemuRunnerError` instead.
    - `rollback` raises `hivemind.hive.snapshot.ledger.SnapshotNotFoundError` for a `SnapshotId`
      this instance never `snapshot()`-ted (or has since evicted for budget).
    - A snapshot that would push `cell.id`'s own disk past `disk_budget_bytes` evicts the oldest
      snapshot for that same Cell first (manifest `[virtual_cells] snapshot_disk_budget_mb`);
      unlike Docker's own eviction, there is no separate image to remove -- an internal qcow2
      snapshot's own space is reclaimed by QEMU itself once nothing references it, so eviction
      here only removes the ledger's own record.

See Also:
    - .claude/roadmap.md step 5.10 for this module's own build instructions.
    - hivemind.hive.backends.qemu.qmp for the documented Windows QMP-over-Unix-socket limitation.
    - hivemind.hive.snapshot.docker for DockerSnapshotter, this module's Docker-backend counterpart.
    - hivemind.hive.snapshot.ledger for SnapshotLedger/SnapshotRecord, this module's shared book.
"""

from __future__ import annotations

from datetime import UTC, timedelta

from hivemind.cell import Cell, SnapshotId
from hivemind.hive.backends.qemu.runner import QemuRunnerPort
from hivemind.hive.snapshot.ledger import (
    SnapshotLedgerPort,
    SnapshotNotFoundError,
    SnapshotRecord,
)
from waggle.clock import Clock
from waggle.ids import CellId

__all__ = ["DEFAULT_RETENTION_S", "QemuSnapshotter"]

DEFAULT_RETENTION_S = (
    3600.0  # One hour; matches [virtual_cells] snapshot_retention_s's own default.
)


class QemuSnapshotter:
    """Snapshotter over QMP `savevm`/`loadvm`, for a Virtual Cell QemuCellBackend provisioned.

    See the module docstring for exactly what an internal qcow2 snapshot captures. Owns one small
    table (codingrules section 8.5, documented): `_tags`, mapping a `SnapshotId` this instance
    minted to the QMP tag `savevm` recorded it under.
    """

    def __init__(
        self,
        runner: QemuRunnerPort,
        ledger: SnapshotLedgerPort,
        clock: Clock,
        *,
        retention_s: float = DEFAULT_RETENTION_S,
        disk_budget_bytes: int | None = None,
    ) -> None:
        """Build a QemuSnapshotter with nothing saved yet.

        Args:
            runner: The same QemuRunnerPort the owning QemuCellBackend provisions through
                (`hivemind.hive.backends.qemu.backend.QemuCellBackend.runner`).
            ledger: The Hive's shared SnapshotLedger, for disk accounting, retention and budget.
            clock: Source of every `taken_at`/`expires_at` timestamp this instance records.
            retention_s: Seconds a snapshot this instance takes survives before `ledger.expire`
                removes its record (manifest `[virtual_cells] snapshot_retention_s`).
            disk_budget_bytes: The most bytes one Cell's own live snapshots may hold before the
                oldest is evicted to make room (manifest `[virtual_cells] snapshot_disk_budget_mb`
                converted to bytes); None means no cap.
        """
        self._runner = runner
        self._ledger = ledger
        self._clock = clock
        self._retention_s = retention_s
        self._disk_budget_bytes = disk_budget_bytes
        self._tags: dict[SnapshotId, str] = {}
        # See hivemind.hive.snapshot.docker.DockerSnapshotter's own identical field for why this
        # exists: a same-timestamp clock must still mint a unique tag per call.
        self._next_seq = 0

    async def snapshot(self, cell: Cell) -> SnapshotId:
        """Take an internal qcow2 snapshot of `cell`'s own overlay disk; see module docstring.

        Args:
            cell: The VIRTUAL Cell to snapshot.

        Returns:
            A SnapshotId `rollback` can later use to restore this point.

        Raises:
            QemuRunnerError: The VM is not running, or QMP reported the command failed (including
                the Windows QMP-over-Unix-socket limitation, module docstring).
        """
        tag = _snapshot_tag(cell.id, self._clock, self._next_seq)
        self._next_seq += 1
        size_bytes = await self._runner.savevm(cell.id, tag)
        await self._evict_if_over_budget(cell.id, size_bytes)
        snapshot_id = SnapshotId(f"snap_qemu_{tag}")
        taken_at = self._clock.now()
        self._ledger.record(
            SnapshotRecord(
                id=snapshot_id,
                cell_id=cell.id,
                taken_at=taken_at,
                bytes_estimate=size_bytes,
                expires_at=taken_at + timedelta(seconds=self._retention_s),
            )
        )
        self._tags[snapshot_id] = tag
        return snapshot_id

    async def rollback(self, cell: Cell, snapshot: SnapshotId) -> None:
        """Restore `cell`'s full device state from the snapshot `snapshot` took; see module docs.

        Args:
            cell: The Cell to roll back.
            snapshot: An id a prior `snapshot` call on this Cell returned.

        Raises:
            SnapshotNotFoundError: `snapshot` was never recorded by this Snapshotter (or has since
                expired or been evicted for budget).
            QemuRunnerError: The VM is not running, or QMP reported the command failed.
        """
        tag = self._tags.get(snapshot)
        if tag is None:
            raise SnapshotNotFoundError(snapshot)
        await self._runner.loadvm(cell.id, tag)

    async def _evict_if_over_budget(self, cell_id: CellId, incoming_bytes: int) -> None:
        """Evict `cell_id`'s oldest snapshot's own ledger record, when over budget.

        No underlying QEMU call is needed (module docstring's own key invariant): an internal
        qcow2 snapshot's own space is reclaimed by QEMU itself once nothing references it.
        """
        evict_id = self._ledger.room_for(cell_id, incoming_bytes, self._disk_budget_bytes)
        if evict_id is None:
            return
        self._tags.pop(evict_id, None)
        self._ledger.delete(evict_id)


def _snapshot_tag(cell_id: CellId, clock: Clock, seq: int) -> str:
    """Build a unique-per-call snapshot tag: `cell_id`, a timestamp, and a per-instance sequence."""
    stamp = clock.now().astimezone(UTC).strftime("%Y%m%dt%H%M%S%f")
    return f"{cell_id}-{stamp}-{seq}"
