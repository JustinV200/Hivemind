"""Run the receiving side of one blob transfer: refuse by rule, then take chunks and verify.

`TransferReceiver` is the receiving end's state machine. On an `Offer` it verifies the manifest's
signature (an unknown signer is `UNKNOWN_SIGNER`, a bad signature `INTEGRITY`) and then applies
every by-rule refusal of ADR-0048 before the first byte moves: a label above the Cell's ceiling, a
tainted blob, an unsupported platform, no room in the resident ceiling. On each `Chunk` it enforces
order (a gap is a `TransferError`, a repeat is acked with the current offset and not appended
twice, which is what makes a lost ack harmless), appends to a spool file rather than memory, and on
the last chunk hashes the whole file against the offer: a mismatch deletes the file, records an
`IntegrityFailure` for the caller to turn into `guard.basket_integrity_failed`, and refuses with
`INTEGRITY`. It does no wire work; `transfer/wire.py` (a later step) maps its values to messages.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.transfer`.
    Driven by the Warden's Basket tick (and the Queen's, for a deposit shipped up); one instance
    per transfer. Calls into `hivemind.basket` (clearance, manifest, errors), `transfer.models`,
    `state`, `spool` and waggle's `Verifier` protocol.

Key invariants:
    - No byte is kept outside the spool file before the whole blob's hash and the manifest
      signature have both verified, and a bad hash deletes the spool file.
    - A repeated chunk is never appended twice: `received_bytes` only ever grows by a chunk that
      starts exactly where it stands.
    - After COMPLETE or REFUSED the receiver accepts nothing new, except that a repeat of a chunk it
      already took is re-acked (the final ack may have been lost).
    - `integrity_failures` holds hashes and stages only, never bytes.

See Also:
    - hivemind.basket.transfer.sender for the other end.
    - hivemind.basket.clearance for `receipt_refusal`, the by-rule checks.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from hivemind.basket.clearance import receipt_refusal
from hivemind.basket.errors import BlobIntegrityError, BlobRefusedError, TransferError
from hivemind.basket.residency import ResidencyDecision
from hivemind.basket.transfer.models import Ack, Chunk, Offer, RefusalCode, Refuse
from hivemind.basket.transfer.spool import Spool
from hivemind.basket.transfer.state import TransferPhase, advance
from hivemind.cell import HoneyClearance, OsFamily
from waggle.codec import Verifier

__all__ = ["IntegrityFailure", "ResidencyPlanner", "TransferReceiver"]

# How a by-rule clearance refusal reads on the wire. A retired blob is one the Hive has pulled, so
# to the receiver it is a blob the sender can no longer supply.
_CODES: dict[str, RefusalCode] = {
    "clearance": RefusalCode.CLEARANCE,
    "tainted": RefusalCode.TAINTED,
    "platform": RefusalCode.PLATFORM,
    "retired": RefusalCode.UNKNOWN_BLOB,
}


class ResidencyPlanner(Protocol):
    """What the receiver needs to know about room: would a blob of this size fit?"""

    async def plan(self, size_bytes: int) -> ResidencyDecision:
        """Say whether a blob of `size_bytes` would fit, changing nothing."""
        ...


@dataclass(frozen=True, slots=True)
class IntegrityFailure:
    """One failed integrity check, for the caller to record as `guard.basket_integrity_failed`."""

    sha256: str  # The blob that failed.
    stage: Literal["manifest", "whole"]  # The signature on the offer, or the hash of all the bytes.
    received_bytes: int  # How many bytes had arrived (0 for a manifest failure).


class TransferReceiver:
    """One blob's receiving side: decide on the offer, then take chunks in order and verify."""

    def __init__(self, spool_dir: Path) -> None:
        """Bind the receiver to its spool directory.

        Args:
            spool_dir: Where partial blobs are written; created on the first chunk.
        """
        self._spool_dir = spool_dir
        self._offer: Offer | None = None
        self._spool: Spool | None = None
        self._phase = TransferPhase.IDLE
        self._received = 0
        self._failures: list[IntegrityFailure] = []
        self._completed: Path | None = None

    @property
    def phase(self) -> TransferPhase:
        """Where this transfer has got to."""
        return self._phase

    @property
    def received_bytes(self) -> int:
        """Contiguous bytes held so far."""
        return self._received

    @property
    def integrity_failures(self) -> tuple[IntegrityFailure, ...]:
        """Every failed integrity check so far, for the caller to turn into trail events."""
        return tuple(self._failures)

    @property
    def completed_path(self) -> Path | None:
        """The verified bytes' file once COMPLETE; the caller hands it to the store."""
        return self._completed

    async def on_offer(
        self,
        offer: Offer,
        verifier: Verifier,
        ceiling: HoneyClearance,
        resident: ResidencyPlanner,
        platform: OsFamily,
    ) -> Ack | Refuse:
        """Decide on an offer: refuse by rule, or accept and say where to start.

        Args:
            offer: The sender's offer.
            verifier: Trusted public keys by node id, for the manifest signature.
            ceiling: The highest label the receiving Cell may hold.
            resident: Answers whether the blob would fit the Cell's resident ceiling.
            platform: The receiving Cell's operating system family.

        Returns:
            An `Ack` whose `received_bytes` is where the sender should start (more than 0 when a
            partial file from an earlier attempt exists), or a `Refuse` naming the rule.

        Raises:
            TransferError: `complete` (the transfer already ended) or `unknown` (an offer for a
                different blob than this receiver is handling).
        """
        self._require_open(offer.sha256)
        refusal = await self._refuse_by_rule(offer, verifier, ceiling, resident, platform)
        if refusal is not None:
            self._phase = advance(self._phase, TransferPhase.REFUSED, offer.sha256)
            return refusal
        # A repeated offer (our ack was lost) keeps the file and the phase; only the first moves.
        if self._offer is None:
            self._offer = offer
            self._spool = Spool(self._spool_dir, offer.sha256)
            self._phase = advance(self._phase, TransferPhase.OFFERED, offer.sha256)
            self._received = await asyncio.to_thread(_resume_offset, offer, self._spool)
        return Ack(sha256=offer.sha256, received_bytes=self._received)

    async def on_chunk(self, chunk: Chunk) -> Ack | Refuse:
        """Take one chunk: ack it, ack a repeat without appending, or verify the whole at the end.

        Args:
            chunk: The next slice of the blob.

        Returns:
            An `Ack` with the contiguous bytes now held (`complete` after the last chunk
            verifies), or `Refuse(INTEGRITY)` when the whole blob did not match its hash.

        Raises:
            TransferError: `unknown` (no offer, or another blob's chunk), `offset` (a gap or
                overlap), `too_large` (runs past the blob's size) or `complete` (a new chunk after
                the transfer ended).
        """
        offer, spool = self._require_chunkable(chunk)
        end = chunk.offset + len(chunk.data)
        if end > offer.size_bytes:
            raise TransferError("too_large", f"chunk for {offer.sha256} runs past the blob's size.")
        # Already held (a resend after a lost ack): answer with where we stand, append nothing.
        if end <= self._received:
            return Ack(
                sha256=offer.sha256,
                received_bytes=self._received,
                complete=self._phase is TransferPhase.COMPLETE,
            )
        # Strictly in order: a chunk must start exactly where the contiguous bytes end.
        if chunk.offset != self._received:
            raise TransferError(
                "offset",
                f"chunk for {offer.sha256} starts at {chunk.offset}, expected {self._received}.",
            )
        await asyncio.to_thread(spool.append, chunk.data)
        self._received = end
        if self._phase is TransferPhase.OFFERED:
            self._phase = advance(self._phase, TransferPhase.TRANSFERRING, offer.sha256)
        if chunk.is_last or end >= offer.size_bytes:
            return await self._verify_whole(offer, spool)
        return Ack(sha256=offer.sha256, received_bytes=self._received)

    def _require_open(self, sha256: str) -> None:
        """Refuse input for another blob, or after the transfer ended."""
        if self._offer is not None and self._offer.sha256 != sha256:
            raise TransferError("unknown", f"this receiver is handling another blob, not {sha256}.")
        if self._phase in (TransferPhase.COMPLETE, TransferPhase.REFUSED):
            raise TransferError("complete", f"transfer of {sha256} already ended.")

    def _require_chunkable(self, chunk: Chunk) -> tuple[Offer, Spool]:
        """Return the standing offer and spool a chunk may be applied to, or raise."""
        if self._offer is None or self._spool is None or self._offer.sha256 != chunk.sha256:
            raise TransferError("unknown", f"no accepted offer for blob {chunk.sha256}.")
        # A refused transfer takes nothing; a completed one may still see a repeated chunk.
        if self._phase is TransferPhase.REFUSED:
            raise TransferError("complete", f"transfer of {chunk.sha256} was refused.")
        return self._offer, self._spool

    async def _verify_whole(self, offer: Offer, spool: Spool) -> Ack | Refuse:
        """Hash the spool file against the offer; keep it on a match, delete it on a mismatch."""
        # Blocking: one pass over the file; disk latency class, bounded by the blob's size.
        digest = await asyncio.to_thread(spool.digest)
        if self._received == offer.size_bytes and digest == offer.sha256:
            self._completed = await asyncio.to_thread(spool.finalize)
            self._phase = advance(self._phase, TransferPhase.COMPLETE, offer.sha256)
            return Ack(sha256=offer.sha256, received_bytes=self._received, complete=True)
        # Wrong length or wrong hash: the bytes are not the blob. Delete them, never resume.
        await asyncio.to_thread(spool.discard)
        self._failures.append(IntegrityFailure(offer.sha256, "whole", self._received))
        self._phase = advance(self._phase, TransferPhase.REFUSED, offer.sha256)
        return Refuse(
            sha256=offer.sha256,
            code=RefusalCode.INTEGRITY,
            reason=f"the received bytes do not hash to {offer.sha256}.",
        )

    async def _refuse_by_rule(
        self,
        offer: Offer,
        verifier: Verifier,
        ceiling: HoneyClearance,
        resident: ResidencyPlanner,
        platform: OsFamily,
    ) -> Refuse | None:
        """Apply the signature check and every by-rule refusal; None means the offer stands."""
        sha256 = offer.sha256
        try:
            offer.manifest.verify(verifier)
        except BlobRefusedError as unknown:
            return Refuse(sha256=sha256, code=RefusalCode.UNKNOWN_SIGNER, reason=str(unknown)[:200])
        except BlobIntegrityError:
            self._failures.append(IntegrityFailure(sha256, "manifest", 0))
            return Refuse(
                sha256=sha256,
                code=RefusalCode.INTEGRITY,
                reason=f"the manifest signature for {sha256} does not verify.",
            )
        rule = receipt_refusal(offer.manifest.blob, ceiling, platform)
        if rule is not None:
            return Refuse(sha256=sha256, code=_CODES[rule.code], reason=rule.reason)
        # Last, because it asks the store: would the blob fit once old blobs are evicted?
        decision = await resident.plan(offer.size_bytes)
        if not decision.admit:
            return Refuse(sha256=sha256, code=RefusalCode.NO_ROOM, reason=decision.reason)
        return None


def _resume_offset(offer: Offer, spool: Spool) -> int:
    """Return how many bytes an earlier attempt left in the spool, discarding an impossible one."""
    held = spool.size()
    # A partial file longer than the blob cannot be a prefix of it: start over.
    if held > offer.size_bytes:
        spool.discard()
        return 0
    return held
