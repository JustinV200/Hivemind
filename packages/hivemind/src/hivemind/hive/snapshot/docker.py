"""Define DockerSnapshotter: Snapshotter over `docker commit`/recreate, for a Docker Virtual Cell.

Roadmap step 5.10: "DockerSnapshotter... Docker commit... document precisely what a Docker commit
does and does not capture." `snapshot()` commits the running container named for `cell.id`
(`hivemind.hive.backends.docker.backend.container_name`'s own deterministic scheme) to a new
image via `hivemind.hive.backends.docker.client.DockerClientPort.commit_container`; `rollback()`
recreates that same container, booted from the committed image, via `recreate_from_image`.

**What a Docker commit captures:** the container's root filesystem exactly as it stands (every
file the image's own writable layer holds) and its image config (env, cmd, entrypoint, the labels
this module stamps). **What it does not capture:** the scratch volume (mounted separately, never
part of any image layer -- a Worker's own scratch writes are untouched by rollback, governed by
`hivemind.supervision.capping.apply`'s REVERSE_DIFF instead) and any in-flight process state (no
CRIU-style checkpoint; a rolled-back container starts fresh at the image's own entrypoint, the way
starting any other container does). This is still a meaningful Capping rollback for the tiers that
use it (`irreversible`, `device_command`, ADR-0018): those proposals are guarded because they might
install a package, edit a file outside scratch, or otherwise change the Cell's own root filesystem
in a way nothing else can undo -- exactly what a root-filesystem commit restores.

Fits into the Hive:
    Layer 3 (sources of Cells), inside `hivemind.hive.snapshot`. Implements `hivemind.cell.
    Snapshotter`; constructed by the composition root (`hivemind.hive.snapshot.snapshotter_for`)
    and injected into a Warden's `hivemind.supervision.capping.gate.GateDeps.snapshotter`
    (roadmap step 5.10's own wiring point). Calls into `hivemind.cell` (Cell, SnapshotId,
    SnapshotUnsupportedError), `hivemind.hive.backends.docker.backend` (container_name),
    `hivemind.hive.backends.docker.client` (DockerClientError, DockerClientPort),
    `hivemind.hive.snapshot.ledger` and waggle only.

Key invariants:
    - `capabilities.can_snapshot` is always True for a Docker backend (ADR-0026), so this class
      never raises `SnapshotUnsupportedError` itself; a genuine daemon failure propagates as
      `DockerClientError` instead, a different failure mode `hivemind.supervision.capping.gate`
      does not special-case (only `SnapshotUnsupportedError` triggers its REVERSE_DIFF fallback).
    - `rollback` raises `hivemind.hive.snapshot.ledger.SnapshotNotFoundError` for a `SnapshotId`
      this instance never `snapshot()`-ted (or has since evicted for budget) -- a different case
      from "this Cell cannot be snapshotted at all".
    - A snapshot that would push `cell.id`'s own disk past `disk_budget_bytes` evicts the oldest
      snapshot for that same Cell first (manifest `[virtual_cells] snapshot_disk_budget_mb`), never
      a snapshot belonging to a different Cell.

See Also:
    - .claude/roadmap.md step 5.10 for this module's own build instructions.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for REVERSE_DIFF vs snapshot
      rollback and why SnapshotUnsupportedError is the one exception the gate catches.
    - hivemind.hive.snapshot.qemu for QemuSnapshotter, this module's QEMU-backend counterpart.
    - hivemind.hive.snapshot.ledger for SnapshotLedger/SnapshotRecord, this module's shared book.
    - hivemind.hive.backends.docker.client for DockerClientPort.commit_container/remove_image/
      recreate_from_image, the three operations this module composes.
"""

from __future__ import annotations

from datetime import UTC, timedelta

from hivemind.cell import Cell, SnapshotId
from hivemind.hive.backends.docker.backend import container_name
from hivemind.hive.backends.docker.client import DockerClientPort
from hivemind.hive.snapshot.ledger import (
    SnapshotLedgerPort,
    SnapshotNotFoundError,
    SnapshotRecord,
)
from waggle.clock import Clock
from waggle.ids import CellId

__all__ = ["DEFAULT_RETENTION_S", "DockerSnapshotter"]

DEFAULT_RETENTION_S = (
    3600.0  # One hour; matches [virtual_cells] snapshot_retention_s's own default.
)
_IMAGE_REPOSITORY = "hivemind-snapshot"  # Every committed image's own repository name.
_LABEL_SNAPSHOT_OF = "hivemind.snapshot_of"  # Stamped on the committed image with its own Cell id.


class DockerSnapshotter:
    """Snapshotter over `docker commit`/recreate, for a Virtual Cell DockerCellBackend provisioned.

    See the module docstring for exactly what a commit captures and does not. Owns one small
    table (codingrules section 8.5, documented): `_images`, mapping a `SnapshotId` this instance
    minted to the image ref `commit_container` returned for it, so `rollback` never has to ask the
    shared `SnapshotLedger` for backend-specific state it was never given.
    """

    def __init__(
        self,
        client: DockerClientPort,
        ledger: SnapshotLedgerPort,
        clock: Clock,
        *,
        retention_s: float = DEFAULT_RETENTION_S,
        disk_budget_bytes: int | None = None,
    ) -> None:
        """Build a DockerSnapshotter with nothing committed yet.

        Args:
            client: The same DockerClientPort the owning DockerCellBackend provisions through
                (`hivemind.hive.backends.docker.backend.DockerCellBackend.client`).
            ledger: The Hive's shared SnapshotLedger, for disk accounting, retention and budget.
            clock: Source of every `taken_at`/`expires_at` timestamp this instance records.
            retention_s: Seconds a snapshot this instance takes survives before `ledger.expire`
                removes its record (manifest `[virtual_cells] snapshot_retention_s`).
            disk_budget_bytes: The most bytes one Cell's own live snapshots may hold before the
                oldest is evicted to make room (manifest `[virtual_cells] snapshot_disk_budget_mb`
                converted to bytes); None means no cap.
        """
        self._client = client
        self._ledger = ledger
        self._clock = clock
        self._retention_s = retention_s
        self._disk_budget_bytes = disk_budget_bytes
        self._images: dict[SnapshotId, str] = {}
        # A same-millisecond FakeClock in tests (or a real one under heavy load) can hand back the
        # same timestamp for two calls in a row; this counter is what keeps _snapshot_tag unique
        # per call regardless, without needing the clock to have actually ticked.
        self._next_seq = 0

    async def snapshot(self, cell: Cell) -> SnapshotId:
        """Commit `cell`'s running container to a new image; see the module docstring.

        Args:
            cell: The VIRTUAL Cell to snapshot.

        Returns:
            A SnapshotId `rollback` can later use to restore this point.

        Raises:
            DockerClientError: The daemon could not commit the container.
        """
        tag = _snapshot_tag(cell.id, self._clock, self._next_seq)
        self._next_seq += 1
        commit = await self._client.commit_container(
            container_name(cell.id),
            repository=_IMAGE_REPOSITORY,
            tag=tag,
            labels={_LABEL_SNAPSHOT_OF: str(cell.id)},
        )
        await self._evict_if_over_budget(cell.id, commit.size_bytes)
        snapshot_id = SnapshotId(f"snap_docker_{tag}")
        taken_at = self._clock.now()
        self._ledger.record(
            SnapshotRecord(
                id=snapshot_id,
                cell_id=cell.id,
                taken_at=taken_at,
                bytes_estimate=commit.size_bytes,
                expires_at=taken_at + timedelta(seconds=self._retention_s),
            )
        )
        self._images[snapshot_id] = commit.image
        return snapshot_id

    async def rollback(self, cell: Cell, snapshot: SnapshotId) -> None:
        """Recreate `cell`'s container from the image `snapshot` committed; see module docstring.

        Args:
            cell: The Cell to roll back.
            snapshot: An id a prior `snapshot` call on this Cell returned.

        Raises:
            SnapshotNotFoundError: `snapshot` was never recorded by this Snapshotter (or has since
                expired or been evicted for budget).
            DockerClientError: The daemon could not recreate the container.
        """
        image = self._images.get(snapshot)
        if image is None:
            raise SnapshotNotFoundError(snapshot)
        await self._client.recreate_from_image(container_name(cell.id), image)

    async def _evict_if_over_budget(self, cell_id: CellId, incoming_bytes: int) -> None:
        """Evict `cell_id`'s oldest snapshot first, when it would push disk past budget."""
        evict_id = self._ledger.room_for(cell_id, incoming_bytes, self._disk_budget_bytes)
        if evict_id is None:
            return
        image = self._images.pop(evict_id, None)
        self._ledger.delete(evict_id)
        if image is not None:
            await self._client.remove_image(image)


def _snapshot_tag(cell_id: CellId, clock: Clock, seq: int) -> str:
    """Build a unique-per-call image tag: `cell_id`, a timestamp, and a per-instance sequence."""
    stamp = clock.now().astimezone(UTC).strftime("%Y%m%dt%H%M%S%f")
    return f"{cell_id}-{stamp}-{seq}"
