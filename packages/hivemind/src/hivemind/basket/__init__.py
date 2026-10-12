"""The Basket: content-addressed, signed, labelled, inert blobs, and how they reach a Cell.

The Basket holds the bytes that work produces and Honey cannot represent (a wheel, a dataset, a
tool package, a model's weights). Every blob is named by its sha256, signed by the Hive key at
deposit (`BlobManifest`), labelled with a clearance from its provenance, and described by exactly
one Honey record, the only way a bee finds it. The canonical copy lives on the Hive Stand
(`SqliteBasketStore`); each Cell keeps the blobs its Warden chose within a disk ceiling
(`ResidentBasket`, `decide_residency`); a blob reaches another Cell through the Hive Stand in
chunks, verified on arrival (`TransferSender`, `TransferReceiver`). The rule that keeps it safe is
that a blob is inert: holding one grants no permission to run it, and nothing here runs, installs
or loads one.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), beside `honey_store`, `supervision`,
    `brood_chamber` and `memory`. Called by the Queen's and Wardens' Basket ticks, the Royal Jelly
    promotion flow and `hive basket` (later steps). Calls into `hivemind.cell`, `hivemind.guard`
    (scanner), `hivemind.pheromone`, `hivemind.common` and waggle; never `honey_store` (the Honey
    record goes through a port), `memory`, `llm` or anything above Layer 2.

Key invariants:
    - A blob is inert: no public name here executes, installs, loads or imports one.
    - A blob's label is never below the higher of its declared label and its provenance floor
      (a Real Cell origin is C2), and a tainted blob is never shipped.
    - The same bytes are one blob; a repeat deposit never lowers a label and writes no second
      Honey record.
    - A receiver keeps no byte outside its spool file until the manifest signature and the whole
      blob's hash have both verified.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the decisions.
    - README.md in this directory for the module map and how to test it.
    - hivemind.honey_store for the Honey records every deposit writes.

Public API:
    - Blob, BlobState, MediaKind, BlobManifest: a blob's description and its signed form.
    - DepositRequest, HoneyRecord, HoneyRecordPort: what a deposit takes and the Honey port.
    - BasketDeposits, DepositWiring, DepositOutcome: the deposit door.
    - BasketStore, SqliteBasketStore, ResidentBlob, Shipment, apply_basket_migrations: persistence.
    - ResidencyDecision, decide_residency, ResidentBasket: per-Cell residency under a ceiling.
    - blob_clearance, blob_floor, receiver_ceiling, may_receive: the pure label rules.
    - CHUNK_BYTES, Offer, Chunk, Ack, Refuse, RefusalCode, TransferSender, TransferReceiver: the
      transfer values and the two state machines.
    - BasketError and its subclasses: the error tree.
"""

from hivemind.basket.clearance import blob_clearance, blob_floor, may_receive, receiver_ceiling
from hivemind.basket.deposits import BasketDeposits, DepositOutcome, DepositWiring
from hivemind.basket.errors import (
    BasketError,
    BlobIntegrityError,
    BlobNotFoundError,
    BlobRefusedError,
    BlobTooLargeError,
    ShipmentNotFoundError,
    TransferError,
)
from hivemind.basket.manifest import BlobManifest
from hivemind.basket.models import (
    Blob,
    BlobState,
    DepositRequest,
    HoneyRecord,
    HoneyRecordPort,
    MediaKind,
)
from hivemind.basket.residency import ResidencyDecision, ResidentBasket, decide_residency
from hivemind.basket.store import (
    BasketStore,
    ResidentBlob,
    Shipment,
    SqliteBasketStore,
    apply_basket_migrations,
)
from hivemind.basket.transfer import (
    CHUNK_BYTES,
    Ack,
    Chunk,
    Offer,
    RefusalCode,
    Refuse,
    TransferReceiver,
    TransferSender,
)

__all__ = [
    "CHUNK_BYTES",
    "Ack",
    "BasketDeposits",
    "BasketError",
    "BasketStore",
    "Blob",
    "BlobIntegrityError",
    "BlobManifest",
    "BlobNotFoundError",
    "BlobRefusedError",
    "BlobState",
    "BlobTooLargeError",
    "Chunk",
    "DepositOutcome",
    "DepositRequest",
    "DepositWiring",
    "HoneyRecord",
    "HoneyRecordPort",
    "MediaKind",
    "Offer",
    "RefusalCode",
    "Refuse",
    "ResidencyDecision",
    "ResidentBasket",
    "ResidentBlob",
    "Shipment",
    "ShipmentNotFoundError",
    "SqliteBasketStore",
    "TransferError",
    "TransferReceiver",
    "TransferSender",
    "apply_basket_migrations",
    "blob_clearance",
    "blob_floor",
    "decide_residency",
    "may_receive",
    "receiver_ceiling",
]
