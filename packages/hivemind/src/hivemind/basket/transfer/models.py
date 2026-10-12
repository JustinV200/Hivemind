"""Define the transfer value models: Offer, Chunk, Ack, Refuse and the refusal codes.

A blob travels between two nodes in four moves: the sender makes an `Offer` (the signed manifest,
for a destination Cell); the receiver answers with an `Ack` (send from `received_bytes`) or a
`Refuse` (a by-rule `RefusalCode`); the sender then sends `Chunk`s, each answered by an `Ack`; the
last `Ack` has `complete` set. These are the Basket's own models, not the Waggle messages:
`transfer/wire.py` maps them to `basket.offer`, `basket.chunk`, `basket.ack` and
`basket.refuse`, so the two state machines stay pure and testable without the wire. A chunk holds at
most `CHUNK_BYTES` raw bytes so that its base64 form fits `waggle.codec.MAX_FRAME_BYTES`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.transfer`.
    Built and read by `sender` and `receiver`; mapped to the wire by `wire`. Calls
    into `hivemind.basket.models` and `manifest` and waggle ids only.

Key invariants:
    - `CHUNK_BYTES * 4 / 3 + 4096 < MAX_FRAME_BYTES`: a base64 chunk plus its envelope always fits
      one frame (asserted by a test).
    - An `Offer` carries the signed manifest only; the receiver takes every fact about the blob
      from the manifest it verifies, never from a separate, unsigned field.
    - No model here ever prints a chunk's bytes (`repr=False`).

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the message flow.
    - docs/waggle/spec.md, the `basket.*` messages these map to.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.basket.manifest import BlobManifest
from hivemind.basket.models import Sha256
from waggle.messages.base import CellIdField

CHUNK_BYTES = 262_144  # 256 KiB raw: its base64 form (about 350 KB) fits the 1 MiB frame cap.
MAX_REASON_CHARS = 200  # A reason is a sentence for the trail, never content.

__all__ = ["CHUNK_BYTES", "MAX_REASON_CHARS", "Ack", "Chunk", "Offer", "RefusalCode", "Refuse"]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # codingrules 8.5: boundary values.


class RefusalCode(Enum):
    """Why a receiver refuses an offer by rule, before the first chunk (ADR-0048)."""

    CLEARANCE = "CLEARANCE"  # The blob's label is above the receiving Cell's ceiling.
    TAINTED = "TAINTED"  # The blob came from an isolated or quarantined source.
    NO_ROOM = "NO_ROOM"  # The Cell's resident ceiling cannot fit it even after evictions.
    UNKNOWN_SIGNER = "UNKNOWN_SIGNER"  # The manifest's signer is not a node the receiver trusts.
    INTEGRITY = "INTEGRITY"  # The signature or the whole blob's hash did not verify.
    PLATFORM = "PLATFORM"  # The blob does not support the Cell's operating system.
    UNKNOWN_BLOB = "UNKNOWN_BLOB"  # The blob is retired or not one the sender can supply.


class Offer(BaseModel):
    """The sender's opening move: "here is a blob, signed, for this Cell"."""

    model_config = _MODEL_CONFIG

    manifest: BlobManifest = Field(description="The blob's signed manifest.")
    destination_cell_id: CellIdField = Field(description="The Cell that should hold the blob.")
    reason: str = Field(
        default="", max_length=MAX_REASON_CHARS, description="Why it is being shipped."
    )

    @property
    def sha256(self) -> str:
        """The offered blob's hash, which names the whole transfer."""
        return self.manifest.blob.sha256

    @property
    def size_bytes(self) -> int:
        """The offered blob's size: how many bytes the receiver should end up with."""
        return self.manifest.blob.size_bytes


class Chunk(BaseModel):
    """One contiguous slice of the blob's bytes, at an offset."""

    model_config = _MODEL_CONFIG

    sha256: Sha256 = Field(description="The blob this slice belongs to.")
    offset: int = Field(ge=0, description="Byte position of the slice's first byte in the blob.")
    data: bytes = Field(
        min_length=1, max_length=CHUNK_BYTES, repr=False, description="The slice's raw bytes."
    )
    is_last: bool = Field(description="True for the slice that ends the blob.")


class Ack(BaseModel):
    """The receiver's "I have this much; send from here", or "done" when `complete`."""

    model_config = _MODEL_CONFIG

    sha256: Sha256 = Field(description="The blob this acknowledges.")
    received_bytes: int = Field(ge=0, description="Contiguous bytes the receiver now holds.")
    complete: bool = Field(default=False, description="True once the whole blob verified.")
    reason: str = Field(default="", max_length=MAX_REASON_CHARS, description="Optional note.")


class Refuse(BaseModel):
    """The receiver's "no", by rule, with a code and a sentence."""

    model_config = _MODEL_CONFIG

    sha256: Sha256 = Field(description="The blob refused.")
    code: RefusalCode = Field(description="Which rule refused it.")
    reason: str = Field(max_length=MAX_REASON_CHARS, description="A sentence for the trail.")
