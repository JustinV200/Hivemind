"""Define the basket family's transfer half: one slice of a blob, and the receiver's ack.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). Once a
receiver has accepted a ``BasketOffer`` (see ``waggle.messages.basket.offer``), the sender streams
the blob as ``BasketChunk`` messages ordered by byte offset, each at most ``MAX_CHUNK_BYTES`` of
raw data so its base64 text still fits one frame (spec section 5), and the receiver answers with
a ``BasketAck`` that says how many contiguous bytes it holds. That count is what makes a transfer
resumable: after a dropped link the sender asks again with the offer, reads ``received_bytes`` from
the ack and continues from there. ``data`` is base64 text rather than ``bytes`` so the decode is
checked by this model's own validator and a refusal names the field; the Basket's other half
(``offer``) is split out by responsibility so each file stays under the codingrules 5.1 size
limit. Every bound is a named constant here or in ``waggle.messages.base``; the number, not the
name, is normative.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Registered by waggle.messages.registry, which maps
    each class to its kind; built by whichever bee sends a blob (the Queen, the central
    orchestrator, or a Warden, the always-on supervisor of one Cell) and answered by the
    receiver; calls into waggle.messages.base only.

Key invariants:
    - No class here carries its kind string; the registry is the only place kinds live.
    - Every message is frozen and forbids extras through WaggleMessage's config.
    - A BasketChunk's data always decodes (standard padded base64) to between one byte and
      MAX_CHUNK_BYTES, so no receiver ever handles an undecodable or oversized chunk.
    - Every rule the spec marks (validator) is a pydantic validator on the class; every rule it
      marks (receiver rule) is deliberately absent, because the receiver enforces it.

See Also:
    - docs/waggle/spec.md section 8.12 for the normative fields, bounds and validators.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the decision behind them.
    - waggle.messages.basket.offer for BasketOffer and BasketRefuse, the family's other half.
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated

from pydantic import AfterValidator, Field

from waggle.messages.base import (
    MAX_CHUNK_BYTES,
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    WaggleMessage,
)

MIN_CHUNK_BYTES = (
    1  # A chunk carries at least one byte: an empty one moves nothing and ends nothing.
)
BASE64_GROUP_BYTES = 3  # Base64 turns every three raw bytes into four characters.
BASE64_GROUP_CHARS = 4  # ...so the encoded length is four characters per started group of three.
MAX_CHUNK_DATA_CHARS = -(-MAX_CHUNK_BYTES // BASE64_GROUP_BYTES) * BASE64_GROUP_CHARS  # 349_528.
MIN_OFFSET = 0  # The first chunk starts at the front of the blob.
MIN_RECEIVED_BYTES = 0  # A receiver that has kept nothing yet acknowledges zero.

__all__ = [
    "BASE64_GROUP_BYTES",
    "BASE64_GROUP_CHARS",
    "MAX_CHUNK_BYTES",
    "MAX_CHUNK_DATA_CHARS",
    "MIN_CHUNK_BYTES",
    "MIN_OFFSET",
    "MIN_RECEIVED_BYTES",
    "BasketAck",
    "BasketChunk",
]

# A reason field, as the catalogue conventions fix it: always named `reason`, always bounded by
# the shared MAX_REASON_CHARS, so the Pheromone Trail (the append-only audit log) records why.
_Reason = Annotated[str, Field(max_length=MAX_REASON_CHARS)]


def _check_chunk_data(value: str) -> str:
    """Return ``value`` when it is padded base64 of 1 to MAX_CHUNK_BYTES raw bytes, else raise."""
    # validate=True refuses any character outside the standard alphabet (a newline, a URL-safe
    # "-" or "_"), so two peers cannot disagree about what a chunk decodes to; binascii.Error is
    # also what a bad pad length raises, and pydantic needs a ValueError to name the field.
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"BasketChunk data is not valid standard base64: {exc}") from exc
    # The character cap already bounds the work above; this is the normative raw-byte bound, and
    # it is checked on the decoded length because padding makes the character count only a proxy.
    if not MIN_CHUNK_BYTES <= len(raw) <= MAX_CHUNK_BYTES:
        raise ValueError(
            f"BasketChunk data must decode to {MIN_CHUNK_BYTES} to {MAX_CHUNK_BYTES} bytes, got "
            f"{len(raw)}."
        )
    return value


# One chunk's payload as text: bounded by characters first (so a hostile peer cannot make the
# decode expensive), then proven to decode to a legal number of raw bytes.
_ChunkData = Annotated[
    str, Field(max_length=MAX_CHUNK_DATA_CHARS), AfterValidator(_check_chunk_data)
]


class BasketChunk(WaggleMessage):
    """Carry one slice of a blob, ordered by byte offset (basket.chunk, a request).

    From the sender of an accepted BasketOffer to its receiver. Answered by a BasketAck saying
    how many contiguous bytes the receiver now holds; the final chunk's ack has ``complete`` true
    once the receiver has verified the digest of the whole and the manifest signature. A chunk
    that fails verification draws a BasketRefuse with code INTEGRITY instead.
    """

    sha256: str = Field(
        pattern=SHA256_PATTERN,
        description="Digest of the complete blob this chunk belongs to; with the envelope "
        "sender it is the key every chunk of one shipment shares.",
    )
    offset: int = Field(
        ge=MIN_OFFSET,
        description="Byte offset of this slice within the blob. A chunk whose offset is not the "
        "receiver's received_bytes is out of order and is re-acknowledged, not kept (receiver "
        "rule).",
    )
    data: _ChunkData = Field(
        description="This slice as standard padded base64 text; decodes to 1 to "
        "MAX_CHUNK_BYTES (262144) raw bytes."
    )
    is_last: bool = Field(
        description="True on the final chunk; the receiver then verifies the digest of the "
        "whole and the manifest signature before keeping a byte (receiver rule)."
    )


class BasketAck(WaggleMessage):
    """Acknowledge an offer or a chunk and say how far the receiver has got (basket.ack).

    A reply from the receiver. Answering a BasketOffer it means "send the chunks" (or "resume
    from received_bytes" when part of the blob is already held); answering a BasketChunk it
    reports the new contiguous length, and ``complete`` is true only after verification of the
    whole blob passed.
    """

    sha256: str = Field(pattern=SHA256_PATTERN, description="The blob acknowledged.")
    received_bytes: int = Field(
        ge=MIN_RECEIVED_BYTES,
        description="Contiguous bytes from offset 0 the receiver holds in its temp file; the "
        "sender resumes from here.",
    )
    complete: bool = Field(
        description="True once the whole blob is held, its sha256 matched and the manifest "
        "signature verified; the blob is then resident."
    )
    reason: _Reason = Field(
        description="A one-line note for the Pheromone Trail; the empty string when there is "
        "nothing to add."
    )
