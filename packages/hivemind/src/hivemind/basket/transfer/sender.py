"""Run the sending side of one blob transfer as a pure state machine.

`TransferSender` makes the `Offer`, then answers each `Ack` with the next slice(s) of bytes from
where the receiver says it is, so a transfer resumes by construction: it keeps no offset of its own,
only the receiver's `received_bytes`. It does no I/O (the bytes come through a `ChunkSource` the
caller supplies) and never touches the wire: `transfer/wire.py` (a later step) turns its values
into `basket.offer` and `basket.chunk` messages. A `Refuse` ends it. Every transfer goes through
the Hive Stand, since nodes connect out to the Queen, so a Warden-to-Warden shipment is two senders.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.transfer`.
    Driven by the Queen's and Wardens' Basket ticks (later steps); calls into
    `hivemind.basket.transfer.models`, `state` and `hivemind.basket.errors` only.

Key invariants:
    - Chunks are cut from the receiver's own `received_bytes`, never from a local counter, so a
      lost ack or a restarted receiver just means the next ack names an earlier offset.
    - No chunk is longer than `CHUNK_BYTES`; only the chunk that ends the blob has `is_last`.
    - After a `Refuse` or the final `Ack` the sender is finished and refuses further input.

See Also:
    - hivemind.basket.transfer.receiver for the other end.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hivemind.basket.errors import BlobIntegrityError, TransferError
from hivemind.basket.manifest import BlobManifest
from hivemind.basket.transfer.models import CHUNK_BYTES, Ack, Chunk, Offer, Refuse
from hivemind.basket.transfer.state import TransferPhase, advance
from waggle.ids import CellId

__all__ = ["BytesChunkSource", "ChunkSource", "TransferSender"]


class ChunkSource(Protocol):
    """Where the sender reads the blob's bytes from, a slice at a time."""

    def read(self, offset: int, length: int) -> bytes:
        """Return exactly `length` bytes starting at `offset`.

        Args:
            offset: Byte position of the first byte wanted.
            length: How many bytes; always within the blob.
        """
        ...


@dataclass(frozen=True, slots=True)
class BytesChunkSource:
    """A `ChunkSource` over bytes already in memory (`BasketStore.open_bytes`'s result)."""

    data: bytes  # The whole blob.

    def read(self, offset: int, length: int) -> bytes:
        """Return `length` bytes of the blob starting at `offset`."""
        return self.data[offset : offset + length]


class TransferSender:
    """One blob's sending side: offer, then chunk on demand, until done or refused."""

    def __init__(self, source: ChunkSource, window: int = 1) -> None:
        """Bind the sender to its bytes.

        Args:
            source: Reads the blob's bytes.
            window: How many chunks to hand back per ack (1 for the request/reply wire).
        """
        self._source = source
        self._window = window
        self._offer: Offer | None = None
        self._phase = TransferPhase.IDLE
        self._refusal: Refuse | None = None

    @property
    def phase(self) -> TransferPhase:
        """Where this transfer has got to."""
        return self._phase

    @property
    def done(self) -> bool:
        """True once the receiver acknowledged the whole blob."""
        return self._phase is TransferPhase.COMPLETE

    @property
    def refusal(self) -> Refuse | None:
        """The receiver's refusal, once one arrived; None otherwise."""
        return self._refusal

    def offer(self, manifest: BlobManifest, destination_cell_id: CellId, reason: str = "") -> Offer:
        """Make the opening offer for a blob.

        Args:
            manifest: The blob's signed manifest.
            destination_cell_id: The Cell that should hold the blob.
            reason: Why it is shipped, for the trail.

        Returns:
            The `Offer` to send; the transfer is now OFFERED.

        Raises:
            TransferError: An offer was already made.
        """
        made = Offer(manifest=manifest, destination_cell_id=destination_cell_id, reason=reason)
        self._phase = advance(self._phase, TransferPhase.OFFERED, made.sha256)
        self._offer = made
        return made

    def on_ack(self, ack: Ack) -> tuple[Chunk, ...] | None:
        """Take the receiver's ack and return the next chunks, or None when the transfer is done.

        Args:
            ack: The receiver's reply to the offer or to the last chunk.

        Returns:
            Up to `window` chunks starting at `ack.received_bytes`; an empty tuple when every
            byte is sent but the receiver has not said complete yet; None once it has.

        Raises:
            TransferError: `unknown` (no offer, or an ack for another blob), `complete` (input
                after a refusal), `offset` (the ack claims bytes past the blob's end, or
                `complete` without all of them).
        """
        offer = self._require_offer(ack.sha256)
        if self._phase is TransferPhase.REFUSED:
            raise TransferError("complete", f"transfer of {offer.sha256} was already refused.")
        if ack.received_bytes > offer.size_bytes:
            raise TransferError(
                "offset", f"ack for {offer.sha256} claims bytes past the blob's end."
            )
        if ack.complete:
            self._finish(offer, ack)
            return None
        # First chunks move the transfer from OFFERED to TRANSFERRING; later acks stay there.
        if self._phase is TransferPhase.OFFERED:
            self._phase = advance(self._phase, TransferPhase.TRANSFERRING, offer.sha256)
        return self._chunks_from(offer, ack.received_bytes)

    def on_refuse(self, refuse: Refuse) -> None:
        """Take the receiver's refusal; the transfer ends.

        Args:
            refuse: The receiver's `Refuse`.

        Raises:
            TransferError: `unknown` (no offer, or another blob's refusal) or `complete` (the
                transfer already finished).
        """
        offer = self._require_offer(refuse.sha256)
        self._phase = advance(self._phase, TransferPhase.REFUSED, offer.sha256)
        self._refusal = refuse

    def _require_offer(self, sha256: str) -> Offer:
        """Return the standing offer if `sha256` is its blob, else raise `unknown`."""
        if self._offer is None or self._offer.sha256 != sha256:
            raise TransferError("unknown", f"no offer for blob {sha256} is standing.")
        return self._offer

    def _finish(self, offer: Offer, ack: Ack) -> None:
        """Handle the receiver's completion ack (idempotent for a repeated one)."""
        if ack.received_bytes != offer.size_bytes:
            raise TransferError("offset", f"complete ack for {offer.sha256} is short of the blob.")
        # A repeated final ack (the first reply was lost and we resent) is already done.
        if self._phase is not TransferPhase.COMPLETE:
            if self._phase is TransferPhase.OFFERED:
                self._phase = advance(self._phase, TransferPhase.TRANSFERRING, offer.sha256)
            self._phase = advance(self._phase, TransferPhase.COMPLETE, offer.sha256)

    def _chunks_from(self, offer: Offer, received: int) -> tuple[Chunk, ...]:
        """Cut up to `window` chunks from `received`, the last one flagged when it ends the blob."""
        chunks: list[Chunk] = []
        start = received
        # Walk forward one CHUNK_BYTES slice at a time until the window is full or the blob ends.
        while start < offer.size_bytes and len(chunks) < self._window:
            length = min(CHUNK_BYTES, offer.size_bytes - start)
            data = self._source.read(start, length)
            if len(data) != length:
                raise BlobIntegrityError(offer.sha256, "its source returned a short read")
            chunks.append(
                Chunk(
                    sha256=offer.sha256,
                    offset=start,
                    data=data,
                    is_last=start + length == offer.size_bytes,
                )
            )
            start += length
        return tuple(chunks)
