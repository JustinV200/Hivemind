"""Define ResidentBlob and Shipment: the two small rows the Basket keeps beside its blobs.

A Cell keeps some blobs resident on its own disk (`ResidentBlob`, one per blob per Cell, with when
it arrived and when it was last used so the oldest-used can be evicted first); the Hive Stand
keeps a queue of blobs it has been asked to ship to a Cell (`Shipment`). Both are plain internal
values (codingrules 8.5), not wire models: the Waggle forms are mapped in `transfer/wire.py`.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.basket.store`.
    Returned by `BasketStore` and read by `hivemind.basket.residency`. Calls into waggle ids only.

Key invariants:
    - A `ResidentBlob` is about the bytes a Cell holds; `size_bytes` is the blob's size, copied so
      residency arithmetic needs no second lookup.
    - A `Shipment` is pending exactly while `completed_at` is None.

See Also:
    - hivemind.basket.residency for the eviction decision made over `ResidentBlob`s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from waggle.ids import CellId

__all__ = ["ResidentBlob", "Shipment"]


@dataclass(frozen=True, slots=True)
class ResidentBlob:
    """One blob a Cell keeps resident on its own disk."""

    sha256: str  # The blob's hash.
    size_bytes: int  # Its size, counted against the Cell's resident-basket ceiling.
    resident_since: datetime  # When it became resident; the tie-break when two were used alike.
    last_used: datetime  # When a bee last read it; eviction takes the oldest first.


@dataclass(frozen=True, slots=True)
class Shipment:
    """A queued request to ship one blob to one Cell, pending until it completes."""

    id: int  # The queue's own row id; `complete_shipment` takes it.
    sha256: str  # The blob to ship.
    destination_cell_id: CellId  # The Cell that should end up holding it.
    reason: str  # Why it is being shipped, for the human and the trail.
    queued_at: datetime  # When the request was made.
    completed_at: datetime | None  # None while the shipment is still pending.
