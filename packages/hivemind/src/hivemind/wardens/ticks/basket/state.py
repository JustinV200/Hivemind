"""Hold a Warden's Basket transfers in flight: the receivers, the senders and the shipping door.

A shipment is several messages over several ticks (an offer, then chunks, each answered), so
something has to remember where each blob has got to between them. `BasketTransfers` is that
memory for one Warden, keyed by the blob's sha256: one `TransferReceiver` per blob arriving (with
the `Offer` it accepted, since the receiver keeps its own private), one `Outgoing` per blob being
shipped up (the `TransferSender`, the offer it opened with, and the future the caller of
`ship_up` awaits), and the deposit
door a Cell's Warden builds lazily over its own store.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Owned by `hivemind.wardens.warden.Warden` (as `_basket`); read and
    written by `receive`, `send`, `door` and `retain` in this package. Calls into
    `hivemind.basket` and nothing else.

Key invariants:
    - Owns its mutable state in place (codingrules section 8.5); everything runs on one event
      loop, so no lock is needed.
    - A hash has at most one receiver and at most one outgoing transfer at a time.
    - An entry is removed the moment its transfer ends, whichever way it ends.

See Also:
    - hivemind.basket.transfer for the two state machines held here.
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the flow.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from hivemind.basket import BasketDeposits, Offer, TransferReceiver, TransferSender

__all__ = ["BasketTransfers", "Outgoing"]


@dataclass(slots=True)
class Outgoing:
    """One blob this Warden is shipping up: its sender and the answer `ship_up` waits on."""

    sender: TransferSender  # Offers, then cuts chunks from each ack the Queen sends.
    # The offer as first sent: sent again once a lost link is back, so the Queen answers with the
    # byte she holds and the shipment carries on from there (`send.redrive_shipments`).
    offer: Offer
    done: asyncio.Future[bool]  # True once the Queen acknowledged the whole blob; False on refusal.


@dataclass(slots=True)
class BasketTransfers:
    """Every transfer one Warden has in flight, plus the deposit door it built for itself."""

    receivers: dict[str, TransferReceiver] = field(default_factory=dict)  # By sha256.
    offers: dict[str, Offer] = field(default_factory=dict)  # The offer each receiver accepted.
    outgoing: dict[str, Outgoing] = field(default_factory=dict)  # By sha256.
    door: BasketDeposits | None = None  # A Cell's own deposit door; None until first needed.
