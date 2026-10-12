"""Re-export the basket family: content-addressed blobs offered, streamed, acknowledged, refused.

Waggle is the Hive's bee-to-bee wire protocol (named after the honeybee waggle dance). The Basket
is the Hive's store of content-addressed, signed blobs; a blob is inert, so bytes travel but
permission does not. ``offer`` holds the negotiation (a blob offered, or refused with a stable
code); ``chunk`` the transfer (a slice of the bytes ordered by offset, and the receiver's
acknowledgement that makes a shipment resumable). This package is the family's face: a caller
imports any of its messages, enums or value models from here without knowing which module defines
them. The bounds each module names stay in that module, because the spec makes the number
normative, not the name.

Fits into the Hive:
    Its own layer (used by every layer in hivemind and by pollen, the lightweight device
    connector), inside the waggle package. Imported by waggle.messages (the catalogue and
    the package face) and by every bee that builds or reads a basket payload; calls into
    nothing beyond its own modules.

Key invariants:
    - Every class the registry registers under ``basket.*`` is re-exported here
      (tests/messages/test_registry_catalogue.py checks it).
    - This file holds re-exports and __all__ only; no message, enum or bound is defined here.

See Also:
    - docs/waggle/spec.md section 8.12 for the family's normative fields and rules.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the decision behind it.
    - waggle.messages.basket.offer and waggle.messages.basket.chunk for the definitions.

Public API:
    - Offer (offer): BasketOffer, BasketRefuse, BasketRefusal.
    - Chunk (chunk): BasketChunk, BasketAck.
"""

from waggle.messages.basket.chunk import BasketAck, BasketChunk
from waggle.messages.basket.offer import BasketOffer, BasketRefusal, BasketRefuse

__all__ = [
    "BasketAck",
    "BasketChunk",
    "BasketOffer",
    "BasketRefusal",
    "BasketRefuse",
]
