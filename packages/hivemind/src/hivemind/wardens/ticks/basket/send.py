"""Ship a blob up to the Queen: offer it, send chunks as her acknowledgements arrive, report done.

A Cell's Warden keeps what its sub-bees retain and the packages it scaffolds in its own resident
store; the Queen's canonical Basket gets a copy only by a shipment (ADR-0048: every transfer goes
through the Hive Stand, since nodes connect out to her). `ship_up` starts one: it checks the blob
is shippable (present, not retired, not tainted), offers its signed manifest in a `basket.offer`
and waits -- on a future a later tick resolves, so no tick ever blocks -- until the Queen has
acknowledged the whole blob or refused it. The tick side is `on_answer`: each `basket.ack` or
`basket.refuse` from the Queen is fed to that blob's `TransferSender`, which cuts the next chunk
from the byte offset she reports, so a shipment resumes by construction after a lost link. The
Hive Stand's Warden shares the Queen's store, so for it shipping is already done.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.basket sub-package. `ship_up` is called by `retain` and by the tool handlers' sandbox
    runner (from background jobs, never a tick); `on_answer` by `handle_basket_item` for a
    Queen-sent `basket.ack` or `basket.refuse`; `redrive_shipments` by the offline return
    (`hivemind.wardens.offline.resume.leave_offline`) once a lost link is back. Calls into
    `hivemind.basket` and waggle.

A link that goes while a shipment is under way does not end it. A chunk the link refuses because
it is down (the offline table has no row for Basket traffic, so a reconnecting link refuses it)
leaves the shipment standing, and `redrive_shipments` sends its offer again once the link is back:
the Queen answers a repeated offer with the byte she holds, so the chunks resume from there, and
a chunk the dying link swallowed unnoticed is covered by the same re-offer. Only a chunk the link
would not take while it is up ends the shipment at once, since nothing will re-drive it.

Key invariants:
    - A tainted or retired blob is never offered.
    - The destination of an offer up is this Warden's own Cell: the Queen receives it as a
      deposit from that Cell (the convention 3G's receiver follows).
    - Every wait is bounded by `[royal_jelly] request_timeout_s` (600 s by default) and the
      transfer is forgotten when it ends, whichever way it ends; an outage inside that bound
      only delays it.
    - Chunks are cut from the receiver's own `received_bytes`, never from a local counter.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the flow.
    - hivemind.basket.transfer.sender for the state machine this drives.
    - hivemind.wardens.ticks.basket.receive for the receiving half.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from hivemind.basket import (
    BasketError,
    BlobState,
    TransferError,
    TransferSender,
    ack_from_wire,
    chunk_to_wire,
    offer_to_wire,
    refuse_from_wire,
)
from hivemind.basket.transfer import BytesChunkSource
from hivemind.common.logging import get_logger
from hivemind.common.tasks import reap
from hivemind.manifest.schema.royal_jelly import DEFAULT_REQUEST_TIMEOUT_S
from hivemind.wardens.links import send_guarded
from hivemind.wardens.ticks.basket.events import own_cell_id, record_basket
from hivemind.wardens.ticks.basket.source import store_of
from hivemind.wardens.ticks.basket.state import Outgoing
from waggle.envelope import wrap
from waggle.messages.basket import BasketAck, BasketRefuse

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

MAX_OFFER_REASON_CHARS = 200  # The Basket's own bound on an offer's reason.

__all__ = ["on_answer", "redrive_shipments", "request_timeout_s", "ship_up"]

log = get_logger(__name__)


def request_timeout_s(warden: Warden) -> float:
    """Return how long a shipment or a sandbox request waits: `[royal_jelly] request_timeout_s`.

    Args:
        warden: The waiting Warden.

    Returns:
        The manifest's figure where this Warden holds the Lab's handles; ten minutes otherwise.
    """
    access = warden._deps.royal_jelly
    return access.config.request_timeout_s if access is not None else DEFAULT_REQUEST_TIMEOUT_S


async def ship_up(warden: Warden, sha256: str, reason: str) -> bool:
    """Ship the blob `sha256` from this Warden's store up to the Queen; True once she holds it.

    Never call this from inside a tick: it waits for acknowledgements that later ticks deliver.

    Args:
        warden: The shipping Warden.
        sha256: The blob to ship; it must be in this Warden's own store.
        reason: Why it is shipped, for the trail (cut to the Basket's bound).

    Returns:
        True when the Queen acknowledged the whole blob; False when it cannot or may not be
        shipped, when she refused it or when she did not answer in time. On the Hive Stand the
        store is already hers, so this is True at once.
    """
    deps = warden._deps
    if deps.royal_jelly is not None:
        return True
    standing = warden._basket.outgoing.get(sha256)
    if standing is not None:
        return await _await_done(warden, sha256, standing)
    outgoing = await _begin(warden, sha256, reason[:MAX_OFFER_REASON_CHARS])
    return outgoing is not None and await _await_done(warden, sha256, outgoing)


async def on_answer(warden: Warden, message: BasketAck | BasketRefuse) -> None:
    """Feed the Queen's `basket.ack` or `basket.refuse` to the shipment it answers.

    Args:
        warden: The shipping Warden.
        message: The Queen's reply to an offer or a chunk this Warden sent.
    """
    outgoing = warden._basket.outgoing.get(message.sha256)
    if outgoing is None:
        # Late (the wait timed out) or never ours: a peer's timing, not an error.
        log.warning("wardens.basket.unmatched_answer", sha256=message.sha256)
        return
    try:
        if isinstance(message, BasketRefuse):
            outgoing.sender.on_refuse(refuse_from_wire(message))
            await _finish(warden, message.sha256, success=False, why=message.code.value)
            return
        chunks = outgoing.sender.on_ack(ack_from_wire(message))
    except (TransferError, BasketError) as exc:
        log.warning("wardens.basket.shipment_failed", sha256=message.sha256, error=str(exc))
        await _finish(warden, message.sha256, success=False, why="protocol")
        return
    if chunks is None:
        await _finish(warden, message.sha256, success=True, why="complete")
        return
    for chunk in chunks:
        # A fresh request per chunk; the Queen answers each with a basket.ack.
        envelope = wrap(chunk_to_wire(chunk), warden._deps.hop, clock=warden._deps.clock)
        if not await send_guarded(warden._deps.queen_link, envelope):
            await _chunk_not_sent(warden, message.sha256)
            return


async def redrive_shipments(warden: Warden) -> None:
    """Send the offer of every standing shipment again: the link to the Queen is back.

    The sender keeps no offset of its own, so nothing here rewinds it: the Queen answers the
    repeated offer with the byte she holds, and the next chunk is cut from there. A re-offer the
    link will not take leaves the shipment standing for the next return, inside its timeout.

    Args:
        warden: The Warden whose link has just come back.
    """
    deps = warden._deps
    for sha256, outgoing in tuple(warden._basket.outgoing.items()):
        log.info("wardens.basket.reoffered", sha256=sha256)
        envelope = wrap(offer_to_wire(outgoing.offer), deps.hop, clock=deps.clock)
        await send_guarded(deps.queen_link, envelope)


async def _chunk_not_sent(warden: Warden, sha256: str) -> None:
    """Decide what a chunk the link would not take means for its shipment."""
    if not warden._deps.queen_link.is_connected:
        # The link is down: the shipment waits for it, and the return re-offers the blob
        # (`redrive_shipments`), so the Queen says where to carry on from. The request timeout
        # still bounds the wait.
        log.info("wardens.basket.shipment_waits_for_link", sha256=sha256)
        return
    # The link is up and still would not take the chunk: nothing will re-drive this, so the
    # caller hears now rather than after the timeout.
    await _finish(warden, sha256, success=False, why="link")


async def _begin(warden: Warden, sha256: str, reason: str) -> Outgoing | None:
    """Check the blob is shippable, register the transfer and send the opening offer."""
    deps, store, cell_id = warden._deps, store_of(warden._deps), own_cell_id(warden)
    if store is None or cell_id is None:
        return None
    try:
        blob, manifest = await store.get(sha256), await store.manifest(sha256)
        data = await store.open_bytes(sha256)
    except BasketError as exc:
        log.warning("wardens.basket.not_shippable", sha256=sha256, error=str(exc))
        return None
    # A tainted blob came from an isolated or quarantined source and is never shipped (ADR-0048).
    if blob.tainted or blob.state is not BlobState.PRESENT:
        log.warning("wardens.basket.refused_to_ship", sha256=sha256, tainted=blob.tainted)
        return None
    sender = TransferSender(BytesChunkSource(data))
    offer = sender.offer(manifest, cell_id, reason)
    outgoing = Outgoing(sender, offer, asyncio.get_running_loop().create_future())
    warden._basket.outgoing[sha256] = outgoing
    if not await send_guarded(
        deps.queen_link, wrap(offer_to_wire(offer), deps.hop, clock=deps.clock)
    ):
        warden._basket.outgoing.pop(sha256, None)
        return None
    await record_basket(
        warden,
        "basket.offered",
        {"sha256": sha256, "size_bytes": blob.size_bytes, "direction": "up"},
    )
    return outgoing


async def _await_done(warden: Warden, sha256: str, outgoing: Outgoing) -> bool:
    """Wait, bounded, for the shipment to end; a timeout forgets it and reports failure."""
    timeout = asyncio.ensure_future(warden._deps.clock.sleep(request_timeout_s(warden)))
    waitables: set[asyncio.Future[Any]] = {outgoing.done, timeout}
    try:
        # External await: the Queen acknowledges each chunk on a later tick; minutes at worst. On
        # timeout the shipment is abandoned and the caller learns it did not arrive.
        await asyncio.wait(waitables, return_when=asyncio.FIRST_COMPLETED)
    finally:
        await reap(timeout)
    if not outgoing.done.done():
        await _finish(warden, sha256, success=False, why="timeout")
    return outgoing.done.result()


async def _finish(warden: Warden, sha256: str, *, success: bool, why: str) -> None:
    """End a shipment: forget it, wake its waiter and record how it ended."""
    outgoing = warden._basket.outgoing.pop(sha256, None)
    if outgoing is None:
        return
    if not outgoing.done.done():
        outgoing.done.set_result(success)
    kind = "basket.shipped" if success else "basket.refused"
    await record_basket(warden, kind, {"sha256": sha256, "code": why, "direction": "up"})
