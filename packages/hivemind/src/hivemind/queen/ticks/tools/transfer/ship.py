"""Ship one Basket blob to one Warden: offer it, then feed it chunks as the acks come back.

A blob travels as `basket.offer`, then `basket.ack` or `basket.refuse`, then `basket.chunk`s each
answered by an ack, the last with `complete` set (ADR-0048). `ship` is the job's half: it makes the
`TransferSender`, sends the offer and awaits a future that the tick's half resolves. `handle_basket_
reply` is that tick half: a Warden's `basket.ack` or `basket.refuse` drives the sender, the next
chunk goes out through the Warden's link, and the final ack (or a refusal, or a sender error)
resolves the future. Nothing waits in the tick: the tick only advances a state machine and sends.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools.transfer`.
    `ship` is called by the sandbox runner, the announcer and the queued-shipment drain;
    `handle_basket_reply` by the tools tick dispatch. Calls into `hivemind.basket` (the store, the
    sender, the wire mapping) and `hivemind.queen.deps` only.

Key invariants:
    - One transfer per (Warden, blob) at a time; a second request for the same pair waits on the
      first one's future instead of starting a second offer.
    - The sender and its future are removed from the lane when `ship` returns, however it ends.
    - Blob bytes are read from the store and sent in chunks; they are never logged or put on the
      trail, which carries hashes and sizes only.
    - A refusal is final for that offer and is recorded as `basket.refused`; a timeout, a closed
      link or a sender error is not final, and the caller may try again.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the flow.
    - hivemind.basket.transfer.sender for the state machine this drives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from enum import Enum

from pydantic import JsonValue

from hivemind.basket import (
    BasketError,
    BlobNotFoundError,
    BlobRefusedError,
    TransferError,
    TransferSender,
    ack_from_wire,
    chunk_to_wire,
    offer_to_wire,
    refuse_from_wire,
)
from hivemind.basket.transfer import BytesChunkSource
from hivemind.basket.transfer.models import MAX_REASON_CHARS
from hivemind.common.logging import get_logger
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.tools.events import record_basket
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import wrap
from waggle.ids import WardenId
from waggle.messages.basket import BasketAck, BasketRefuse

log = get_logger(__name__)

__all__ = ["ShipResult", "handle_basket_reply", "ship", "ship_blob"]


class ShipResult(Enum):
    """How one shipment ended."""

    SHIPPED = "SHIPPED"  # The Warden holds the blob, verified.
    REFUSED = (
        "REFUSED"  # The Warden (or the Basket) refused it by rule; trying again changes nothing.
    )
    FAILED = "FAILED"  # A timeout, a closed link or a protocol error; trying again may work.


async def ship_blob(deps: QueenDeps, link: WardenLink, sha256: str, reason: str) -> bool:
    """Ship `sha256` to `link`'s Warden and report whether it arrived and verified.

    Args:
        deps: The Queen's collaborators.
        link: The receiving Warden's link; its Cell is the destination.
        sha256: The blob to ship; the Basket must hold it.
        reason: Why, for the offer and the trail.

    Returns:
        True once the Warden acknowledged the whole blob; False otherwise (see `ship`).
    """
    return await ship(deps, link, sha256, reason) is ShipResult.SHIPPED


async def ship(deps: QueenDeps, link: WardenLink, sha256: str, reason: str) -> ShipResult:
    """Offer the blob and wait, up to `[royal_jelly] request_timeout_s`, for it to be accepted.

    Args:
        deps: The Queen's collaborators; `royal_jelly` supplies the store and the timeout.
        link: The receiving Warden's link.
        sha256: The blob to ship.
        reason: Why it is shipped; cut to the transfer's bound.

    Returns:
        SHIPPED, REFUSED (the blob is unknown, retired, or the Warden refused it by rule) or
        FAILED (the link is gone, the wait timed out or the protocol broke).
    """
    access = deps.royal_jelly
    if access is None:
        return ShipResult.FAILED
    lane, key = deps.tool_lane, (link.warden_id, sha256)
    standing = lane.shipped.get(key)
    if standing is not None:
        # The same blob is already on its way to this Warden: share that transfer's outcome.
        return await _outcome(standing, None, access.config.request_timeout_s)
    # Registered before the first await, so a second request meets this one instead of racing it.
    future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    lane.shipped[key] = future
    try:
        return await _offer(deps, link, (sha256, reason), future)
    finally:
        if not future.done():
            future.set_result(False)  # Anyone sharing this transfer learns it did not arrive.
        lane.transfers.pop(key, None)
        lane.shipped.pop(key, None)


async def _offer(
    deps: QueenDeps, link: WardenLink, blob: tuple[str, str], future: asyncio.Future[bool]
) -> ShipResult:
    """Load the blob, send the offer and wait for the outcome the tick's acks resolve."""
    sha256, reason = blob
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - `ship` returned early without one.
    try:
        # Local SQLite and a file read; bounded by the store's own busy timeout and the blob's size.
        manifest = await access.basket.manifest(sha256)
        data = await access.basket.open_bytes(sha256)
    except (BlobNotFoundError, BlobRefusedError) as exc:
        log.warning("queen.tools.ship_unknown_blob", sha256=sha256, code=exc.code)
        return ShipResult.REFUSED
    except BasketError as exc:
        log.warning("queen.tools.ship_unreadable", sha256=sha256, code=exc.code)
        return ShipResult.FAILED
    sender = TransferSender(BytesChunkSource(data))
    offer = sender.offer(manifest, link.cell.id, reason[:MAX_REASON_CHARS])
    deps.tool_lane.transfers[(link.warden_id, sha256)] = sender
    if not await link.send(wrap(offer_to_wire(offer), link.hop, clock=deps.clock)):
        return ShipResult.FAILED
    facts: dict[str, JsonValue] = {
        "sha256": sha256,
        "size_bytes": offer.size_bytes,
        "warden_id": link.warden_id,
    }
    await record_basket(deps, "basket.offered", link.cell.id, facts)
    return await _outcome(future, sender, access.config.request_timeout_s)


async def _outcome(
    future: asyncio.Future[bool], sender: TransferSender | None, timeout_s: float
) -> ShipResult:
    """Wait for a transfer's future; classify a False by whether the receiver refused."""
    try:
        async with asyncio.timeout(timeout_s):
            # Shielded: giving up here must not cancel a future another waiter still shares.
            arrived = await asyncio.shield(future)
    except TimeoutError:
        log.warning("queen.tools.ship_timed_out", timeout_s=timeout_s)
        return ShipResult.FAILED
    if arrived:
        return ShipResult.SHIPPED
    refused = sender is not None and sender.refusal is not None
    return ShipResult.REFUSED if refused else ShipResult.FAILED


async def handle_basket_reply(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> None:
    """Drive a shipment with the Warden's `basket.ack` or `basket.refuse`.

    Args:
        deps: The Queen's collaborators; `tool_lane.transfers` holds the sender being driven.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered item; `item.principal` is the replying Warden and `item.payload` a
            `BasketAck` or `BasketRefuse`.
    """
    payload = item.payload
    link = wardens.get(WardenId(item.principal))
    if link is None or not isinstance(payload, BasketAck | BasketRefuse):
        return  # The Warden detached since, or this is not a reply: nothing to drive.
    key = (link.warden_id, payload.sha256)
    sender = deps.tool_lane.transfers.get(key)
    if sender is None:
        # A late ack for a shipment that timed out or finished: harmless, and worth a debug line.
        log.debug("queen.tools.basket_reply_unmatched", sha256=payload.sha256)
        return
    try:
        if isinstance(payload, BasketRefuse):
            await _refused(deps, link, sender, payload)
        else:
            await _advance(deps, link, sender, payload)
    except TransferError as exc:
        # The Warden's ack contradicted the offer (a gap past the end, a stranger's blob): end it.
        log.warning("queen.tools.ship_protocol_error", sha256=payload.sha256, code=exc.code)
        _settle(deps, key, False)


async def _refused(
    deps: QueenDeps, link: WardenLink, sender: TransferSender, payload: BasketRefuse
) -> None:
    """Record a Warden's refusal, end the sender and resolve the shipment as not arrived."""
    refusal = refuse_from_wire(payload)
    sender.on_refuse(refusal)
    facts: dict[str, JsonValue] = {
        "sha256": payload.sha256,
        "code": refusal.code.value,
        "reason": refusal.reason,
    }
    await record_basket(deps, "basket.refused", link.cell.id, facts)
    _settle(deps, (link.warden_id, payload.sha256), False)


async def _advance(
    deps: QueenDeps, link: WardenLink, sender: TransferSender, payload: BasketAck
) -> None:
    """Feed an ack to the sender: send the next chunk, or settle the shipment when it is done."""
    key = (link.warden_id, payload.sha256)
    chunks = sender.on_ack(ack_from_wire(payload))
    if chunks is None:
        facts: dict[str, JsonValue] = {
            "sha256": payload.sha256,
            "size_bytes": payload.received_bytes,
        }
        await record_basket(deps, "basket.shipped", link.cell.id, facts)
        _settle(deps, key, True)
        return
    for chunk in chunks:
        if not await link.send(wrap(chunk_to_wire(chunk), link.hop, clock=deps.clock)):
            _settle(deps, key, False)  # The link closed mid-transfer: the job reports it failed.
            return


def _settle(deps: QueenDeps, key: tuple[WardenId, str], arrived: bool) -> None:
    """Resolve a shipment's future once; a repeat (a late second ack) changes nothing."""
    future = deps.tool_lane.shipped.get(key)
    if future is not None and not future.done():
        future.set_result(arrived)
