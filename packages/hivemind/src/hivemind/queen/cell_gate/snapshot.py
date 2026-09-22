"""Define CellSnapshotHandler: answer a Warden's own CellSnapshotRequest/CellRollbackRequest.

A Virtual Cell's own Warden runs inside the Cell (ADR-0027) and so cannot reach the host's Docker
daemon or QEMU process to snapshot or roll back its own Cell; only the Queen, on the Hive Stand,
can. `CellSnapshotHandler` is the Queen-side half of the relay (`hivemind.wardens.snapshot_relay.
RelaySnapshotter` is the Warden-side half): for a Cell `hivemind.hive.lifecycle.CellLifecycle`
tracks, it builds that Cell's own backend's `Snapshotter` (`hivemind.hive.snapshot.snapshotter_for`)
and calls it; for a Cell the lifecycle does not know (never provisioned by this Hive, already torn
down, or a Real Cell, which this relay was never meant for), it answers with an error reply instead
of raising, so a bad or stale request from a Warden never crashes the Queen's own dispatch.

One Snapshotter per backend is cached and reused (`_snapshotters`, keyed by the backend object
`hivemind.hive.registry.BackendRegistry.get` already caches per name): `DockerSnapshotter`/
`QemuSnapshotter` each keep their own private map from a `SnapshotId` they minted to the
image/tag needed to roll it back (`_images`/`_tags`, neither persisted in the shared
`SnapshotLedgerPort`), so a *fresh* Snapshotter built for a later rollback request would never find
an id an earlier request's own instance minted -- reusing one instance per backend is what makes a
`CellSnapshotRequest` followed later by a `CellRollbackRequest` for the same Cell actually work.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.cell_gate`. Built by
    the composition root (`hivemind.cli.compose.virtual_cells`) alongside the rest of the Virtual
    Cell wiring, over the same `CellLifecycle` and a `hivemind.hive.snapshot.SnapshotLedgerPort`;
    called by `hivemind.queen.cell_gate.listener.CellListener` for every `CellSnapshotRequest`/
    `CellRollbackRequest` it drains off a Warden's own connection. Calls into `hivemind.cell`
    (SnapshotId, SnapshotUnsupportedError), `hivemind.hive.backends.base` (CellBackend),
    `hivemind.hive.lifecycle` (CellLifecycle), `hivemind.hive.snapshot` (SnapshotLedgerPort,
    SnapshotNotFoundError, snapshotter_for), waggle and the `waggle.messages.cell` family only.

Key invariants:
    - A Cell `lifecycle.snapshot_target` does not recognise always answers with `error` set, never
      raises: an unknown or stale `cell_id` is the ordinary case of a Warden asking about a Cell
      that has since been destroyed, not a bug.
    - Every Snapshotter this handler builds is cached by its own backend object and reused for
      every later request naming a Cell on that same backend (module docstring's own key reason).

See Also:
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for the Snapshotter contract.
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md for why the relay
      exists at all.
    - hivemind.wardens.snapshot_relay for RelaySnapshotter, the Warden-side half of this relay.
    - hivemind.hive.lifecycle for CellLifecycle.snapshot_target, this handler's one lookup.
    - hivemind.hive.snapshot for snapshotter_for, the factory this handler calls per backend.
"""

from __future__ import annotations

from hivemind.cell import SnapshotId, Snapshotter, SnapshotUnsupportedError
from hivemind.hive.backends.base import CellBackend
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.snapshot import SnapshotLedgerPort, SnapshotNotFoundError, snapshotter_for
from waggle.clock import Clock
from waggle.messages.cell.snapshot import (
    CellRollbackReply,
    CellRollbackRequest,
    CellSnapshotReply,
    CellSnapshotRequest,
)

__all__ = ["CellSnapshotHandler"]

_UNKNOWN_CELL = "Cell {cell_id} is not a Virtual Cell this Hive's own lifecycle currently tracks."


class CellSnapshotHandler:
    """Answer a Warden's own CellSnapshotRequest/CellRollbackRequest over the Cell listener.

    Owns one small cache (codingrules section 8.5, documented): `_snapshotters`, one Snapshotter
    per backend object, built lazily and reused (module docstring's own key reason).
    """

    def __init__(self, lifecycle: CellLifecycle, ledger: SnapshotLedgerPort, clock: Clock) -> None:
        """Build a CellSnapshotHandler over `lifecycle`'s own tracked Cells.

        Args:
            lifecycle: Answers which backend and Cell a `cell_id` names, if any.
            ledger: The Hive's shared SnapshotLedgerPort, handed to every Snapshotter this
                handler builds.
            clock: Injected time source, handed to every Snapshotter this handler builds.
        """
        self._lifecycle = lifecycle
        self._ledger = ledger
        self._clock = clock
        self._snapshotters: dict[CellBackend, Snapshotter] = {}

    async def snapshot(self, request: CellSnapshotRequest) -> CellSnapshotReply:
        """Snapshot the Cell `request` names, or answer with why it could not be.

        Args:
            request: The Warden's own CellSnapshotRequest.

        Returns:
            A CellSnapshotReply carrying the new snapshot's id, or an error.
        """
        target = self._lifecycle.snapshot_target(request.cell_id)
        if target is None:
            return CellSnapshotReply(
                cell_id=request.cell_id,
                snapshot_id=None,
                error=_UNKNOWN_CELL.format(cell_id=request.cell_id),
            )
        backend, cell = target
        snapshotter = self._snapshotter_for(backend)
        try:
            snapshot_id = await snapshotter.snapshot(cell)
        except SnapshotUnsupportedError as exc:
            return CellSnapshotReply(cell_id=request.cell_id, snapshot_id=None, error=str(exc))
        return CellSnapshotReply(cell_id=request.cell_id, snapshot_id=str(snapshot_id), error=None)

    async def rollback(self, request: CellRollbackRequest) -> CellRollbackReply:
        """Roll the Cell `request` names back to its own snapshot, or answer with why not.

        Args:
            request: The Warden's own CellRollbackRequest.

        Returns:
            A CellRollbackReply carrying `ok` and, on failure, an error.
        """
        target = self._lifecycle.snapshot_target(request.cell_id)
        if target is None:
            return CellRollbackReply(
                cell_id=request.cell_id,
                ok=False,
                error=_UNKNOWN_CELL.format(cell_id=request.cell_id),
            )
        backend, cell = target
        snapshotter = self._snapshotter_for(backend)
        try:
            await snapshotter.rollback(cell, SnapshotId(request.snapshot_id))
        except (SnapshotUnsupportedError, SnapshotNotFoundError) as exc:
            return CellRollbackReply(cell_id=request.cell_id, ok=False, error=str(exc))
        return CellRollbackReply(cell_id=request.cell_id, ok=True, error=None)

    def _snapshotter_for(self, backend: CellBackend) -> Snapshotter:
        """Return this backend's own cached Snapshotter, building it on first use."""
        cached = self._snapshotters.get(backend)
        if cached is not None:
            return cached
        built = snapshotter_for(backend, self._ledger, self._clock)
        self._snapshotters[backend] = built
        return built
