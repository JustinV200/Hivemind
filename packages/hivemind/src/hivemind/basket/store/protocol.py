"""Define the BasketStore protocol: durable blobs, their residency and their shipment queue.

The Basket's persistence is deliberately small and deliberately inert: it can deposit bytes, hand
them back (hash-verified), list and retire blobs, track which blobs a Cell keeps resident and queue
shipments. It has no method that runs, installs, loads or imports a blob: holding a blob grants
nothing, and the only reader that turns bytes into code is the Comb tool runner, which accepts a
hash only through a registry row (ADR-0048). `tests/unit/basket/test_inert.py` asserts this
surface by reflection.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket`. Implemented
    by `SqliteBasketStore`; called by `hivemind.basket.deposits`, `residency` and, in later
    steps, the Queen's and Wardens' Basket ticks and the `hive basket` commands. Calls into
    `hivemind.basket.models` and `store.rows` only.

Key invariants:
    - `open_bytes` returns bytes whose sha256 equals the argument, or raises; a corrupted file is
      never returned.
    - A deposit of bytes already held returns the existing blob with `is_new` False and does not
      rewrite the file; it may only raise the stored label (clearance up, taint on), never lower.
    - `retire` keeps the row and the bytes; `open_bytes` then refuses with `reason_code`
      "retired".

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md.
    - hivemind.basket.store.sqlite for the durable implementation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from hivemind.basket.manifest import BlobManifest
from hivemind.basket.models import Blob, BlobState, MediaKind
from hivemind.basket.store.rows import ResidentBlob, Shipment
from waggle.ids import CellId

__all__ = ["BasketStore"]


class BasketStore(Protocol):
    """Persist blobs and the per-Cell residency and shipment records beside them."""

    async def deposit(
        self, request_bytes: bytes, blob: Blob, manifest: BlobManifest
    ) -> tuple[Blob, bool]:
        """Store `request_bytes` under `blob.sha256`, deduplicating by hash.

        Args:
            request_bytes: The blob's bytes; their sha256 and length must match `blob`.
            blob: The description to keep; for a repeat deposit, carries the merged label.
            manifest: The signed manifest of `blob`; stored beside it.

        Returns:
            `(blob, is_new)`: the stored blob and whether this call created it. A repeat returns
            the existing blob (its label raised if `blob`'s ranks higher or newly tainted).

        Raises:
            BlobIntegrityError: The bytes do not match `blob`, or `manifest` describes another blob.
            BlobTooLargeError: The bytes are over the store's cap.
        """
        ...

    async def get(self, sha256: str) -> Blob:
        """Return the blob with this hash.

        Raises:
            BlobNotFoundError: No such blob.
        """
        ...

    async def manifest(self, sha256: str) -> BlobManifest:
        """Return the signed manifest stored for this blob.

        Raises:
            BlobNotFoundError: No such blob.
        """
        ...

    async def open_bytes(self, sha256: str) -> bytes:
        """Return the blob's bytes after checking their sha256 again.

        Raises:
            BlobNotFoundError: No such blob.
            BlobRefusedError: `reason_code` "retired": the blob has been withdrawn.
            BlobIntegrityError: The file is missing or its bytes no longer match the hash.
        """
        ...

    async def list_blobs(
        self,
        media_kind: MediaKind | None = None,
        state: BlobState | None = None,
        origin_cell_id: CellId | None = None,
    ) -> tuple[Blob, ...]:
        """List blobs, oldest deposit first, optionally narrowed by kind, state or origin Cell."""
        ...

    async def retire(self, sha256: str, reason: str) -> Blob:
        """Withdraw a blob: keep the row and bytes, refuse readers. Idempotent.

        Args:
            sha256: The blob to retire.
            reason: Why, for the human; stored on the row, never trailed as text.

        Returns:
            The blob, now RETIRED.

        Raises:
            BlobNotFoundError: No such blob.
        """
        ...

    async def resident(self, cell_id: CellId) -> tuple[ResidentBlob, ...]:
        """Return the blobs this Cell keeps resident, oldest `last_used` first."""
        ...

    async def mark_resident(self, cell_id: CellId, sha256: str, since: datetime) -> None:
        """Record that a Cell holds a blob, or that it was just used (`last_used` becomes `since`).

        Raises:
            BlobNotFoundError: No such blob.
        """
        ...

    async def evict(self, cell_id: CellId, sha256: str) -> None:
        """Forget that a Cell holds a blob. Idempotent; the blob itself is untouched."""
        ...

    async def resident_bytes(self, cell_id: CellId) -> int:
        """Return the total size in bytes of the blobs this Cell keeps resident."""
        ...

    async def queue_shipment(
        self, sha256: str, destination_cell_id: CellId, reason: str
    ) -> Shipment:
        """Queue a request to ship a blob to a Cell.

        Raises:
            BlobNotFoundError: No such blob.
        """
        ...

    async def pending_shipments(self) -> tuple[Shipment, ...]:
        """Return every shipment not yet completed, oldest first."""
        ...

    async def complete_shipment(self, shipment_id: int) -> None:
        """Mark a shipment completed. Idempotent for one already completed.

        Raises:
            ShipmentNotFoundError: No shipment has this id.
        """
        ...
