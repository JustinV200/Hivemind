"""Map the transfer value models to and from the `basket.*` Waggle messages, and nothing else.

The two transfer state machines speak `Offer`, `Chunk`, `Ack` and `Refuse`, which hold raw bytes
and a parsed manifest. The wire speaks `BasketOffer`, `BasketChunk`, `BasketAck` and
`BasketRefuse`, which hold base64 text and the manifest as JSON text. This module is the pure,
total translation between the two: eight functions, no I/O, no state. The manifest travels as its
JSON text and is parsed here but never trusted here; the receiver re-verifies its signature
before it keeps a byte. The offer's separate claim fields (hash, size, label, taint...) exist so a
receiver can refuse by rule cheaply, so `offer_from_wire` refuses an offer whose claims disagree
with the manifest they sit beside: the manifest is the only fact the receiver may rely on.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.transfer`.
    Called by the Queen's and Wardens' Basket ticks (later steps) at the edge where a message is
    wrapped or unwrapped. Calls into `hivemind.basket` (models, manifest, errors) and waggle's
    basket messages only.

Key invariants:
    - Every `*_to_wire` / `*_from_wire` pair round-trips: `from_wire(to_wire(x)) == x`.
    - A chunk's `data` is standard padded base64; a decode failure is impossible for a message
      that passed `BasketChunk` validation, so `chunk_from_wire` cannot fail on one.
    - `offer_from_wire` never returns an offer whose claim fields differ from its manifest.
    - A `RefusalCode` and a `BasketRefusal` carry the same seven names, one to one.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the message flow.
    - docs/waggle/spec.md section 8.12 for the wire fields these map to.
    - hivemind.basket.transfer.models for the value models.
"""

from __future__ import annotations

import base64

from pydantic import ValidationError

from hivemind.basket.errors import TransferError
from hivemind.basket.manifest import BlobManifest
from hivemind.basket.transfer.models import (
    MAX_REASON_CHARS,
    Ack,
    Chunk,
    Offer,
    RefusalCode,
    Refuse,
)
from hivemind.cell import HoneyClearance, OsFamily
from waggle.messages import labels as wire_labels
from waggle.messages.basket import BasketAck, BasketChunk, BasketOffer, BasketRefusal, BasketRefuse

__all__ = [
    "ack_from_wire",
    "ack_to_wire",
    "chunk_from_wire",
    "chunk_to_wire",
    "offer_from_wire",
    "offer_to_wire",
    "refuse_from_wire",
    "refuse_to_wire",
]


def offer_to_wire(offer: Offer) -> BasketOffer:
    """Build the `basket.offer` payload for an offer.

    Args:
        offer: The sender's offer; its manifest is already signed.

    Returns:
        A message whose claim fields are copied from the manifest's blob and whose
        `manifest_json` is the manifest itself, so claims and manifest agree by construction.
    """
    blob = offer.manifest.blob
    # The wire tag is lowercase snake_case (a descriptive word), the enum's value in lower case;
    # the label enums are separate classes on each side of the wire, so they map by value.
    return BasketOffer(
        sha256=blob.sha256,
        size_bytes=blob.size_bytes,
        media_kind=blob.media_kind.value.lower(),
        media_type=blob.media_type,
        platform_tags=tuple(wire_labels.OsFamily(tag.value) for tag in blob.platform_tags),
        clearance=wire_labels.HoneyClearance(blob.clearance.value),
        origin_cell_id=blob.origin_cell_id,
        origin_task_id=blob.origin_task_id,
        origin_bee=blob.origin_bee,
        tainted=blob.tainted,
        manifest_json=offer.manifest.to_json(),
        destination_cell_id=offer.destination_cell_id,
        reason=offer.reason,
    )


def offer_from_wire(message: BasketOffer) -> Offer:
    """Rebuild an `Offer` from a received `basket.offer`, refusing claims the manifest contradicts.

    Args:
        message: The validated wire message; untrusted until the receiver verifies the signature.

    Returns:
        The offer, carrying the parsed (not yet verified) manifest.

    Raises:
        TransferError: `manifest_json` is not a well-formed manifest, or a claim field differs
            from the manifest's own blob (a sender bug or an attempt to mislead a cheap check).
    """
    try:
        manifest = BlobManifest.from_json(message.manifest_json)
    except (ValidationError, ValueError) as exc:
        raise TransferError(
            "unknown", f"basket.offer for {message.sha256} has a bad manifest: {exc}"
        ) from exc
    # Every claim a receiver may refuse on must be the manifest's own, since the signature covers
    # the manifest and nothing else; a mismatch is caught here, before any rule reads a claim.
    mismatched = _mismatched_claims(message, manifest)
    if mismatched:
        raise TransferError(
            "unknown",
            f"basket.offer for {message.sha256} disagrees with its manifest on: "
            f"{', '.join(mismatched)}.",
        )
    return Offer(
        manifest=manifest,
        destination_cell_id=message.destination_cell_id,
        reason=_clip(message.reason),
    )


def _clip(reason: str) -> str:
    """Shorten a peer's reason to the Basket's own bound; it only ever feeds the trail."""
    # The wire allows longer reasons than the value models do; clipping keeps a chatty peer from
    # turning a harmless note into a validation failure.
    return reason[:MAX_REASON_CHARS]


def _mismatched_claims(message: BasketOffer, manifest: BlobManifest) -> list[str]:
    """Name each claim field of `message` that differs from the manifest's blob."""
    blob = manifest.blob
    claims = {
        "sha256": (message.sha256, blob.sha256),
        "size_bytes": (message.size_bytes, blob.size_bytes),
        "media_kind": (message.media_kind, blob.media_kind.value.lower()),
        "media_type": (message.media_type, blob.media_type),
        "platform_tags": (
            tuple(OsFamily(tag.value) for tag in message.platform_tags),
            blob.platform_tags,
        ),
        "clearance": (HoneyClearance(message.clearance.value), blob.clearance),
        "origin_cell_id": (message.origin_cell_id, blob.origin_cell_id),
        "origin_task_id": (message.origin_task_id, blob.origin_task_id),
        "origin_bee": (message.origin_bee, blob.origin_bee),
        "tainted": (message.tainted, blob.tainted),
    }
    return [name for name, (claimed, actual) in claims.items() if claimed != actual]


def chunk_to_wire(chunk: Chunk) -> BasketChunk:
    """Build the `basket.chunk` payload: the slice's bytes as standard padded base64 text."""
    return BasketChunk(
        sha256=chunk.sha256,
        offset=chunk.offset,
        data=base64.b64encode(chunk.data).decode("ascii"),
        is_last=chunk.is_last,
    )


def chunk_from_wire(message: BasketChunk) -> Chunk:
    """Rebuild a `Chunk` from a received `basket.chunk`, decoding its base64 text."""
    # `BasketChunk` already proved the text decodes to 1..MAX_CHUNK_BYTES, so this cannot fail;
    # validate=True keeps this decode as strict as that validator's.
    return Chunk(
        sha256=message.sha256,
        offset=message.offset,
        data=base64.b64decode(message.data, validate=True),
        is_last=message.is_last,
    )


def ack_to_wire(ack: Ack) -> BasketAck:
    """Build the `basket.ack` payload from an acknowledgement."""
    return BasketAck(
        sha256=ack.sha256,
        received_bytes=ack.received_bytes,
        complete=ack.complete,
        reason=ack.reason,
    )


def ack_from_wire(message: BasketAck) -> Ack:
    """Rebuild an `Ack` from a received `basket.ack`."""
    return Ack(
        sha256=message.sha256,
        received_bytes=message.received_bytes,
        complete=message.complete,
        reason=_clip(message.reason),
    )


def refuse_to_wire(refuse: Refuse) -> BasketRefuse:
    """Build the `basket.refuse` payload, mapping the refusal code by name."""
    # Both enums carry the same seven names (a test pins it), so the lookup is by name.
    return BasketRefuse(
        sha256=refuse.sha256, code=BasketRefusal[refuse.code.name], reason=refuse.reason
    )


def refuse_from_wire(message: BasketRefuse) -> Refuse:
    """Rebuild a `Refuse` from a received `basket.refuse`, mapping the code by name."""
    return Refuse(
        sha256=message.sha256, code=RefusalCode[message.code.name], reason=_clip(message.reason)
    )
