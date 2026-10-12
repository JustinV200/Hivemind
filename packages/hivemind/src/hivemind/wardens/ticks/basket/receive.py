"""Receive a blob a Queen ships down: refuse by rule, take chunks, verify, and keep it resident.

The Queen sends a Warden's Cell a blob (a tool package, a wheel, a dataset) as `basket.offer`,
`basket.chunk`s and acknowledgements (ADR-0048). This module is the Warden's half. On an offer it
asks one `TransferReceiver` per blob to refuse by rule before a byte moves (clearance above the
Cell's ceiling, tainted, no room, unknown signer, unsupported platform); on each chunk it spools
and acknowledges; on the last it verifies the hash of the whole and the manifest's Hive-key
signature, and only then stores the bytes, makes them resident and records `basket.received`. A
blob that fails verification is discarded, written to the trail as `guard.basket_integrity_failed`
and answered with `basket.refuse(INTEGRITY)`. Every reply is a fresh envelope correlated to the
message it answers, and a chunk repeated after a lost ack is simply acknowledged again.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. Called by `handle_basket_item` for a Queen-sent offer or chunk.
    Calls into `hivemind.basket` (the receiver, the clearance rule, the wire mapping) and waggle.

Key invariants:
    - Nothing is kept outside the spool file until the whole blob's hash and the manifest
      signature have both verified; the Warden stores the bytes from the verified file.
    - The Warden holds a blob only for its own Cell: an offer addressed elsewhere is refused.
    - A corrupt, truncated or misordered transfer never leaves a receiver standing: the entry is
      dropped the moment it is refused.
    - Nothing here awaits a model or the Queen; every wait is a local disk or SQLite call.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the rules refused by.
    - hivemind.basket.transfer.receiver for the state machine this drives.
    - hivemind.wardens.ticks.basket.send for the Warden's own shipping up.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from hivemind.basket import (
    Ack,
    BasketError,
    BasketStore,
    Chunk,
    Offer,
    RefusalCode,
    Refuse,
    ResidentBasket,
    TransferError,
    TransferReceiver,
    ack_to_wire,
    chunk_from_wire,
    offer_from_wire,
    receiver_ceiling,
    refuse_to_wire,
)
from hivemind.cell import Cell
from hivemind.common.logging import get_logger
from hivemind.wardens.links import send_guarded
from hivemind.wardens.ticks.basket.door import node_keys
from hivemind.wardens.ticks.basket.events import record_basket, record_integrity_failures
from hivemind.wardens.ticks.basket.source import store_of
from waggle.codec import Verifier
from waggle.envelope import wrap
from waggle.ids import MessageId
from waggle.messages.basket import BasketChunk, BasketOffer

if TYPE_CHECKING:
    from hivemind.supervision.attendant import InboxItem
    from hivemind.wardens.warden import Warden

SPOOL_SUBDIR = Path("basket") / "incoming"  # Under the lease's scratch: partial blobs, owner-only.
MAX_REASON_CHARS = 200  # The Basket's own bound on a reason; peers' prose is cut to it.

__all__ = ["SPOOL_SUBDIR", "on_chunk", "on_offer"]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _Site:
    """Everything about this Warden's own Cell a receiver needs, gathered once per message."""

    cell: Cell  # Its tier and operating system decide what it may receive.
    resident: ResidentBasket  # Answers room, and keeps the blob once verified.
    store: BasketStore  # Where the verified bytes are written.
    verifier: Verifier  # Trusts the key that signs the manifests it is shipped.
    spool_dir: Path  # Where a partial blob waits, below the lease's scratch.


async def on_offer(warden: Warden, item: InboxItem, message: BasketOffer) -> None:
    """Answer a Queen-sent `basket.offer`: acknowledge it, or refuse it by rule.

    Args:
        warden: The receiving Warden (its transfers table is read and written directly).
        item: The ordered inbox item; its id is what the reply correlates to.
        message: The offer; untrusted until the manifest verifies.
    """
    reply = await _decide_offer(warden, message)
    await _reply(warden, MessageId(item.id), reply)


async def on_chunk(warden: Warden, item: InboxItem, message: BasketChunk) -> None:
    """Take one `basket.chunk`: acknowledge it, finish the blob on the last, or refuse it.

    Args:
        warden: The receiving Warden.
        item: The ordered inbox item; its id is what the reply correlates to.
        message: The chunk of a blob an earlier offer was accepted for.
    """
    transfers = warden._basket
    receiver = transfers.receivers.get(message.sha256)
    if receiver is None:
        # No offer stands (refused, finished or never made): the sender must offer again.
        reply: Ack | Refuse = _refuse(message.sha256, RefusalCode.UNKNOWN_BLOB, "no offer stands.")
    else:
        reply = await _take_chunk(warden, receiver, chunk_from_wire(message))
    await _reply(warden, MessageId(item.id), reply)


async def _decide_offer(warden: Warden, message: BasketOffer) -> Ack | Refuse:
    """Return the Ack or Refuse for an offer, recording what it learned."""
    sha256 = message.sha256
    try:
        offer = offer_from_wire(message)
    except TransferError as exc:
        # A manifest that does not parse, or claims the manifest contradicts, is not a blob.
        return _refuse(sha256, RefusalCode.INTEGRITY, str(exc))
    site = _site(warden)
    if site is None:
        return _refuse(sha256, RefusalCode.NO_ROOM, "this Warden holds no resident Basket.")
    if offer.destination_cell_id != site.cell.id:
        return _refuse(sha256, RefusalCode.UNKNOWN_BLOB, "the offer is addressed to another Cell.")
    if await site.resident.is_resident(sha256):
        # A repeated shipment (its last ack was lost): everything is already here.
        return Ack(sha256=sha256, received_bytes=offer.size_bytes, complete=True, reason="resident")
    return await _consult_receiver(warden, offer, site)


async def _consult_receiver(warden: Warden, offer: Offer, site: _Site) -> Ack | Refuse:
    """Ask a (new or standing) receiver for this blob whether the offer stands."""
    transfers = warden._basket
    receiver = transfers.receivers.get(offer.sha256) or TransferReceiver(site.spool_dir)
    try:
        answer = await receiver.on_offer(
            offer,
            site.verifier,
            receiver_ceiling(site.cell.comb_shield),
            site.resident,
            site.cell.capabilities.os,
        )
    except TransferError as exc:
        return _refuse(offer.sha256, RefusalCode.UNKNOWN_BLOB, str(exc))
    if isinstance(answer, Refuse):
        await _refused(warden, receiver, answer)
        return answer
    transfers.receivers[offer.sha256] = receiver
    transfers.offers[offer.sha256] = offer
    return answer


async def _take_chunk(warden: Warden, receiver: TransferReceiver, chunk: Chunk) -> Ack | Refuse:
    """Apply one chunk to its receiver; keep the blob when this one completes it."""
    try:
        answer = await receiver.on_chunk(chunk)
    except TransferError as exc:
        if exc.reason_code == "offset":
            # Out of order or repeated: say where we are so the sender resumes from there.
            return Ack(sha256=chunk.sha256, received_bytes=receiver.received_bytes, reason="offset")
        refusal = _refuse(chunk.sha256, RefusalCode.INTEGRITY, str(exc))
        await _refused(warden, receiver, refusal)
        return refusal
    if isinstance(answer, Refuse):
        await _refused(warden, receiver, answer)
        return answer
    if answer.complete:
        return await _keep(warden, receiver, answer)
    return answer


async def _keep(warden: Warden, receiver: TransferReceiver, done: Ack) -> Ack | Refuse:
    """Store a verified blob, make it resident and record `basket.received`."""
    transfers = warden._basket
    offer, site = transfers.offers[done.sha256], _site(warden)
    path = receiver.completed_path
    if site is None or path is None:
        return _refuse(done.sha256, RefusalCode.NO_ROOM, "this Warden can no longer keep blobs.")
    try:
        # Local disk and SQLite: bounded by the blob's size; the store re-hashes what it writes.
        data = await asyncio.to_thread(path.read_bytes)
        blob, _is_new = await site.store.deposit(data, offer.manifest.blob, offer.manifest)
        decision = await site.resident.admit(blob)
    except (BasketError, OSError) as exc:
        refusal = _refuse(done.sha256, RefusalCode.INTEGRITY, f"the blob could not be kept: {exc}")
        await _refused(warden, receiver, refusal)
        return refusal
    if not decision.admit:
        refusal = _refuse(done.sha256, RefusalCode.NO_ROOM, decision.reason)
        await _refused(warden, receiver, refusal)
        return refusal
    await asyncio.to_thread(path.unlink, missing_ok=True)
    _forget(warden, done.sha256)
    await record_basket(
        warden,
        "basket.received",
        {"sha256": done.sha256, "size_bytes": blob.size_bytes, "clearance": blob.clearance.value},
    )
    return done


async def _refused(warden: Warden, receiver: TransferReceiver, refuse: Refuse) -> None:
    """Record a refusal on the trail (and any integrity failure) and forget the transfer."""
    await record_integrity_failures(warden, receiver)
    await record_basket(
        warden,
        "basket.refused",
        {"sha256": refuse.sha256, "code": refuse.code.value, "direction": "in"},
    )
    _forget(warden, refuse.sha256)


def _forget(warden: Warden, sha256: str) -> None:
    """Drop a transfer's receiver and offer: it ended, whichever way."""
    warden._basket.receivers.pop(sha256, None)
    warden._basket.offers.pop(sha256, None)


def _refuse(sha256: str, code: RefusalCode, reason: str) -> Refuse:
    """Build a Refuse whose reason is cut to the Basket's own bound."""
    return Refuse(sha256=sha256, code=code, reason=reason[:MAX_REASON_CHARS])


def _site(warden: Warden) -> _Site | None:
    """Gather the facts a receiver needs, or None when this Warden cannot receive at all."""
    deps, cell, lease = warden._deps, warden._cell, warden._lease
    store, keys = store_of(deps), node_keys(deps)
    if deps.resident_basket is None or cell is None or lease is None:
        return None
    if store is None or keys is None:
        return None
    return _Site(
        cell=cell,
        resident=deps.resident_basket,
        store=store,
        verifier=keys.verifier,
        spool_dir=lease.scratch_root / SPOOL_SUBDIR,
    )


async def _reply(warden: Warden, request_id: MessageId, reply: Ack | Refuse) -> None:
    """Send the Queen the `basket.ack` or `basket.refuse` that answers `request_id`."""
    payload = refuse_to_wire(reply) if isinstance(reply, Refuse) else ack_to_wire(reply)
    envelope = wrap(payload, warden._deps.hop, clock=warden._deps.clock, correlation_id=request_id)
    # A Queen link that has gone is logged by send_guarded; she re-offers after reconnecting.
    await send_guarded(warden._deps.queen_link, envelope)
