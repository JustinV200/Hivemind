"""Provide SqliteBasketStore, the durable BasketStore: SQLite metadata plus a byte directory.

The Basket's rows (`basket_blobs`, `basket_residency`, `basket_shipments`) live in the Hive's one
SQLite file (ADR-0006) and its bytes in a directory below `root` (`files.BlobFiles`), the Comb's
own pattern. Every database call runs on this store's own `ConnectionThread`, one hop per call,
serialised by an `asyncio.Lock`, mirroring `SqliteLeavingsStore`; the SQL itself lives in
`store/sql/`, so this class stays a thin, readable list of the protocol's methods. Dedupe is by
hash, `open_bytes` re-hashes what it reads and refuses a retired blob, and nothing here can run,
load or install a blob (ADR-0048).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store`. Built
    by a composition root (`hivemind.cli.stores.open_basket_store`, a later step) over the Hive
    database and a directory on the Hive Stand, or on a Cell's own volume for its resident set.
    Calls into `hivemind.basket.store.sql`, `files`, `migrate` and `hivemind.common.sqlite`.

Key invariants:
    - Every SQLite call runs on the store's `ConnectionThread`, never the event loop.
    - A blob row exists only after its bytes are on disk, so `open_bytes` finds a file for every
      row a clean shutdown left behind.
    - `max_bytes` caps one deposit; a larger one is `BlobTooLargeError`, never a truncated write.

See Also:
    - docs/adr/0006-sqlite-as-the-single-hive-store.md and docs/adr/0048-basket-inert-blobs-
      residency-and-transfer.md for the decisions followed.
    - hivemind.cell.leavings.store_sqlite for the pattern this mirrors.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from hivemind.basket.errors import BlobRefusedError, BlobTooLargeError
from hivemind.basket.manifest import BlobManifest
from hivemind.basket.models import MAX_DEPOSIT_BYTES, Blob, BlobState, MediaKind
from hivemind.basket.store.files import BlobFiles
from hivemind.basket.store.migrate import apply_basket_migrations
from hivemind.basket.store.rows import ResidentBlob, Shipment
from hivemind.basket.store.sql import blobs, residency, shipments
from hivemind.common.sqlite import ConnectionThread
from waggle.clock import Clock
from waggle.ids import CellId

__all__ = ["SqliteBasketStore"]


class SqliteBasketStore:
    """The durable BasketStore: one connection, one byte directory, one lock per instance."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        root: Path,
        clock: Clock,
        max_bytes: int = MAX_DEPOSIT_BYTES,
    ) -> None:
        """Wrap an already-migrated connection. Prefer `create` over calling this directly.

        Args:
            connection: An open connection whose schema already has the Basket's tables.
            root: Directory for the byte tree; created on the first deposit.
            clock: Stamps retirements, shipments and migrations.
            max_bytes: The largest single deposit this store accepts.
        """
        self._connection = connection
        self._files = BlobFiles(root)
        self._clock = clock
        self._max_bytes = max_bytes
        # One thread per connection (hivemind.common.sqlite.ConnectionThread): a cancelled await
        # can never leave a transaction open under the next caller's BEGIN.
        self._thread = ConnectionThread("hive-basket")
        # Serialises every method on this instance, so a read-then-write pair is one step.
        self._lock = asyncio.Lock()

    @classmethod
    async def create(
        cls,
        connection: sqlite3.Connection,
        root: Path,
        clock: Clock,
        max_bytes: int = MAX_DEPOSIT_BYTES,
    ) -> SqliteBasketStore:
        """Apply the Basket's migrations to `connection`, then wrap it.

        Args:
            connection: An open connection from `hivemind.common.sqlite.connect`.
            root: Directory for the byte tree.
            clock: Injected clock, used for migration timestamps and later stamps.
            max_bytes: The largest single deposit this store accepts.

        Returns:
            A store whose tables exist and are current.
        """
        # Blocking: at most one transaction per pending migration (usually none, once current).
        await asyncio.to_thread(apply_basket_migrations, connection, clock)
        return cls(connection, root, clock, max_bytes)

    async def deposit(
        self, request_bytes: bytes, blob: Blob, manifest: BlobManifest
    ) -> tuple[Blob, bool]:
        """Store the bytes and rows, deduplicating by hash; see BasketStore.deposit."""
        # Cheap and first: an oversized write must not even reach the disk.
        if len(request_bytes) > self._max_bytes:
            raise BlobTooLargeError(len(request_bytes), self._max_bytes)
        async with self._lock:
            # Blocking: one file write (fsync) and one transaction; disk latency class.
            return await self._thread.run(
                blobs.deposit_blob, self._connection, self._files, request_bytes, blob, manifest
            )

    async def get(self, sha256: str) -> Blob:
        """Return the blob with this hash; see BasketStore.get."""
        async with self._lock:
            row = await self._thread.run(blobs.require_row, self._connection, sha256)
        return blobs.blob_from_row(row)

    async def manifest(self, sha256: str) -> BlobManifest:
        """Return the stored signed manifest; see BasketStore.manifest."""
        async with self._lock:
            row = await self._thread.run(blobs.require_row, self._connection, sha256)
        return blobs.manifest_from_row(row)

    async def open_bytes(self, sha256: str) -> bytes:
        """Return the blob's verified bytes, refusing a retired blob; see BasketStore.open_bytes."""
        blob = await self.get(sha256)
        # A retired blob keeps its bytes for the human, but no bee or Cell is handed them.
        if blob.state is BlobState.RETIRED:
            raise BlobRefusedError("retired", f"blob {sha256} has been retired.")
        # Blocking: one file read and hash; disk latency class, bounded by max_bytes.
        return await self._thread.run(self._files.read, sha256)

    async def list_blobs(
        self,
        media_kind: MediaKind | None = None,
        state: BlobState | None = None,
        origin_cell_id: CellId | None = None,
    ) -> tuple[Blob, ...]:
        """List blobs, oldest deposit first; see BasketStore.list_blobs."""
        async with self._lock:
            return await self._thread.run(
                blobs.list_rows, self._connection, media_kind, state, origin_cell_id
            )

    async def retire(self, sha256: str, reason: str) -> Blob:
        """Withdraw a blob, keeping its row and bytes; see BasketStore.retire."""
        async with self._lock:
            return await self._thread.run(
                blobs.retire_blob, self._connection, sha256, reason, self._clock.now()
            )

    async def resident(self, cell_id: CellId) -> tuple[ResidentBlob, ...]:
        """Return a Cell's resident blobs, least recently used first."""
        async with self._lock:
            return await self._thread.run(residency.list_resident, self._connection, cell_id)

    async def mark_resident(self, cell_id: CellId, sha256: str, since: datetime) -> None:
        """Record a Cell as holding a blob, or refresh its last use; see BasketStore."""
        async with self._lock:
            await self._thread.run(
                residency.mark_resident, self._connection, cell_id, sha256, since
            )

    async def evict(self, cell_id: CellId, sha256: str) -> None:
        """Forget that a Cell holds a blob; see BasketStore.evict."""
        async with self._lock:
            await self._thread.run(residency.evict_resident, self._connection, cell_id, sha256)

    async def resident_bytes(self, cell_id: CellId) -> int:
        """Return the total size of a Cell's resident blobs."""
        async with self._lock:
            return await self._thread.run(residency.sum_resident_bytes, self._connection, cell_id)

    async def queue_shipment(
        self, sha256: str, destination_cell_id: CellId, reason: str
    ) -> Shipment:
        """Queue a shipment of a known blob; see BasketStore.queue_shipment."""
        async with self._lock:
            return await self._thread.run(
                shipments.queue_shipment,
                self._connection,
                sha256,
                destination_cell_id,
                reason,
                self._clock.now(),
            )

    async def pending_shipments(self) -> tuple[Shipment, ...]:
        """Return every shipment not yet completed, oldest first."""
        async with self._lock:
            return await self._thread.run(shipments.list_pending, self._connection)

    async def complete_shipment(self, shipment_id: int) -> None:
        """Mark a shipment completed; see BasketStore.complete_shipment."""
        async with self._lock:
            await self._thread.run(
                shipments.complete_shipment, self._connection, shipment_id, self._clock.now()
            )
