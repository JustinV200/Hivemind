"""Receive a blob a Warden ships up: verify it, re-deposit it canonically, and acknowledge it.

A Warden that wants to keep an artefact, or hand over a tool package it scaffolded, sends
`basket.offer` and then `basket.chunk`s (ADR-0048); the Queen is the receiver. A
`TransferReceiver` per (Warden, blob) refuses by rule before the first byte (an unknown signer, a
tainted blob, a label above the Hive Stand's ceiling), spools the chunks into
`<basket root>/incoming/`, and on the last chunk checks the hash of the whole and the manifest's
signature. The offer's manifest is signed by that Warden's own node key (or, for the in-process Hive
Stand Warden, by the Hive key); once it verified, the bytes are re-deposited through
`RoyalJellyAccess.deposits`, so the stored blob carries the Hive's own signature, the provenance
the Queen knows about the link (its Cell, whether it is borrowed, its tier), and its one Honey
record. Only then is the final `basket.ack(complete)` sent.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools.
    transfer`. Called by the tools tick dispatch for a Warden's `basket.offer` and `basket.chunk`;
    calls into `hivemind.basket` (receiver, deposit door, wire mapping), `hivemind.queen.deps`
    and waggle only.

Key invariants:
    - Provenance comes from the Queen's record of the link, never from the offer: the origin Cell
      is `link.cell.id`, borrowed-ness is `link.cell.is_borrowed` and the tier is the Cell's own
      `comb_shield`; the offer contributes only its declared label, taint, kind and platforms.
    - The sender is not told `complete` until the deposit succeeded, so a refused deposit is a
      `control.error` on its last chunk rather than a silent loss.
    - A failed integrity check is `guard.basket_integrity_failed` and the spool file is gone.
    - Nothing here logs or records blob bytes, titles or summaries.

See Also:
    - docs/adr/0048-basket-inert-blobs-residency-and-transfer.md for the flow and the rules.
    - hivemind.basket.transfer.receiver for the state machine this drives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from pydantic import JsonValue

from hivemind.basket import (
    BasketError,
    DepositRequest,
    TransferError,
    ack_to_wire,
    chunk_from_wire,
    offer_from_wire,
    receiver_ceiling,
    refuse_to_wire,
)
from hivemind.basket.models import MAX_SUMMARY_CHARS, MAX_TITLE_CHARS
from hivemind.basket.residency import ResidencyDecision
from hivemind.basket.transfer import Ack, Offer, Refuse, TransferReceiver
from hivemind.cell import CombShieldLevel, OsFamily
from hivemind.common.logging import get_logger
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.tools.events import record_basket, record_integrity_failure
from hivemind.queen.ticks.tools.replies import refuse, reply
from hivemind.royal_jelly import RoyalJellyAccess
from hivemind.supervision.attendant import InboxItem
from waggle.ids import MessageId, WardenId
from waggle.messages.basket import BasketChunk, BasketOffer
from waggle.signing import Ed25519Verifier

BAD_TRANSFER = "hive.basket.bad_transfer"  # An offer or chunk the receiver could not take.
DEPOSIT_REFUSED = "hive.basket.deposit_refused"  # The deposit door refused the verified blob.
UNAVAILABLE = "hive.basket.unavailable"  # No Basket is wired to this Hive.
INCOMING_DIR = "incoming"  # The spool directory below the Basket's (or the scratch) root.
_STAND_TIER = CombShieldLevel.MEADOW  # The Hive Stand is no Night Veil Cell: it holds up to C2.

log = get_logger(__name__)

__all__ = ["BAD_TRANSFER", "DEPOSIT_REFUSED", "UNAVAILABLE", "handle_basket_inbound", "spool_dir"]


class _AnyRoom:
    """A `ResidencyPlanner` that always admits: the Hive Stand keeps every blob until retired."""

    async def plan(self, size_bytes: int) -> ResidencyDecision:
        """Admit a blob of any size (the deposit door enforces `[basket] max_blob_bytes`)."""
        del size_bytes  # The Queen's store has no resident ceiling to fit it under.
        return ResidencyDecision(True, (), "the Hive Stand keeps every blob.")


def spool_dir(access: RoyalJellyAccess, fallback: Path) -> Path:
    """Return where partial uploads are spooled: `<basket root>/incoming`, else below `fallback`.

    Args:
        access: The Lab's handles; `basket_config.root` is the Basket's byte root when set.
        fallback: A directory the Queen may write (the Hive Stand's scratch root), used when
            the manifest names no Basket root and so the resolved default is not known here.
    """
    root = access.basket_config.root
    return (root if root is not None else fallback) / INCOMING_DIR


async def handle_basket_inbound(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> None:
    """Take a Warden's `basket.offer` or `basket.chunk`: answer it and, when whole, deposit it.

    Args:
        deps: The Queen's collaborators; `royal_jelly` holds the deposit door and the verifier.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered item; `item.principal` is the sending Warden and `item.id` the envelope
            id every reply is correlated to.
    """
    link = wardens.get(WardenId(item.principal))
    if link is None:
        return  # The Warden detached since it sent this: no link to answer on.
    refused = (item.id, item.payload_kind)
    if deps.royal_jelly is None:
        await refuse(deps, link, refused, UNAVAILABLE, "No Basket is wired to this Hive.")
        return
    try:
        if isinstance(item.payload, BasketOffer):
            await _on_offer(deps, link, item, item.payload)
        elif isinstance(item.payload, BasketChunk):
            await _on_chunk(deps, link, item, item.payload)
    except TransferError as exc:
        # An offer that contradicts its manifest, a chunk out of order or for no offer.
        _forget(deps, (link.warden_id, getattr(item.payload, "sha256", "")))
        await refuse(deps, link, refused, BAD_TRANSFER, f"The transfer was not taken: {exc.code}.")


async def _on_offer(deps: QueenDeps, link: WardenLink, item: InboxItem, wire: BasketOffer) -> None:
    """Decide on an offer: refuse by rule, or accept and say where to start."""
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - the caller refused a Hive with no Basket.
    offer = offer_from_wire(wire)
    key = (link.warden_id, offer.sha256)
    lane = deps.tool_lane
    receiver = lane.receipts.get(key)
    if receiver is None:
        receiver = TransferReceiver(spool_dir(access, deps.scratch_root))
        lane.receipts[key] = receiver
    lane.offers[key] = offer
    # Local file work on the spool and SQLite reads; a signature check and a few rules, no model.
    result = await receiver.on_offer(
        offer,
        await _verifier(deps, access, link),
        receiver_ceiling(_STAND_TIER),
        _AnyRoom(),
        offer.manifest.blob.platform_tags[0]
        if offer.manifest.blob.platform_tags
        else OsFamily.LINUX,
    )
    await _answer(deps, link, item, receiver, result)


async def _on_chunk(deps: QueenDeps, link: WardenLink, item: InboxItem, wire: BasketChunk) -> None:
    """Append a chunk; on the last one, deposit the verified blob before the final ack."""
    key = (link.warden_id, wire.sha256)
    lane = deps.tool_lane
    receiver, offer = lane.receipts.get(key), lane.offers.get(key)
    if receiver is None or offer is None:
        raise TransferError("unknown", f"no accepted offer for blob {wire.sha256}.")
    result = await receiver.on_chunk(chunk_from_wire(wire))
    if isinstance(result, Ack) and result.complete:
        stored = await _deposit(deps, link, item, (receiver, offer))
        if not stored:
            return  # The deposit door refused; the Warden was told, and the transfer is over.
    await _answer(deps, link, item, receiver, result)


async def _answer(
    deps: QueenDeps,
    link: WardenLink,
    item: InboxItem,
    receiver: TransferReceiver,
    result: Ack | Refuse,
) -> None:
    """Record integrity failures and a refusal, tidy the lane, and send the ack or refusal."""
    for failure in receiver.integrity_failures:
        await record_integrity_failure(deps, link.warden_id, failure)
    sha256 = result.sha256
    done = isinstance(result, Refuse) or result.complete
    if isinstance(result, Refuse):
        facts: dict[str, JsonValue] = {
            "sha256": sha256,
            "code": result.code.value,
            "reason": result.reason,
        }
        await record_basket(deps, "basket.refused", link.cell.id, facts)
    if done:
        _forget(deps, (link.warden_id, sha256))
    wire = refuse_to_wire(result) if isinstance(result, Refuse) else ack_to_wire(result)
    await reply(deps, link, wire, MessageId(item.id))


async def _deposit(
    deps: QueenDeps, link: WardenLink, item: InboxItem, upload: tuple[TransferReceiver, Offer]
) -> bool:
    """Re-deposit a verified upload through the Basket's door; tell the Warden if it is refused."""
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - the caller refused a Hive with no Basket.
    receiver, offer = upload
    path = receiver.completed_path
    if path is None:
        raise TransferError("unknown", f"blob {offer.sha256} was acknowledged but has no file.")
    # Disk latency class, bounded by the blob's size (itself bounded by `[basket] max_blob_bytes`).
    content = await asyncio.to_thread(path.read_bytes)
    try:
        outcome = await access.deposits.deposit(_request(link, offer, content))
    except BasketError as exc:
        for event in exc.events:
            await deps.trail.record(event)
        _forget(deps, (link.warden_id, offer.sha256))
        text = f"The verified blob {offer.sha256} was not kept: {exc.code}."
        await refuse(deps, link, (item.id, item.payload_kind), DEPOSIT_REFUSED, text)
        return False
    finally:
        await asyncio.to_thread(path.unlink, missing_ok=True)
    for event in outcome.events:
        await deps.trail.record(event)
    facts: dict[str, JsonValue] = {
        "sha256": offer.sha256,
        "size_bytes": offer.size_bytes,
        "is_new": outcome.is_new,
    }
    await record_basket(deps, "basket.received", link.cell.id, facts)
    return True


def _request(link: WardenLink, offer: Offer, content: bytes) -> DepositRequest:
    """Build the deposit: the offer's declared facts, the Queen's own record of its origin."""
    blob = offer.manifest.blob
    reason = " ".join(offer.reason.split())
    return DepositRequest(
        content=content,
        title=f"{blob.media_kind.value.lower()} shipped from Cell {link.cell.id}"[:MAX_TITLE_CHARS],
        summary=(
            reason or f"A {blob.media_kind.value.lower()} shipped by Warden {link.warden_id}."
        )[:MAX_SUMMARY_CHARS],
        media_kind=blob.media_kind,
        media_type=blob.media_type,
        platform_tags=blob.platform_tags,
        declared=blob.clearance,
        origin_cell_id=link.cell.id,
        origin_task_id=blob.origin_task_id,
        origin_bee=link.warden_id,
        from_borrowed_cell=link.cell.is_borrowed,
        tier=link.cell.comb_shield,
        tainted=blob.tainted,
    )


async def _verifier(deps: QueenDeps, access: RoyalJellyAccess, link: WardenLink) -> Ed25519Verifier:
    """Trust the Hive key, and the key the Queen holds for this link's node, nothing else.

    The node's key comes from whoever holds one for the link (`QueenDeps.uplink_keys`: the Cell
    gate for a Virtual Cell, the swarm registry for a colonized device), read afresh on every
    offer so a key revoked since the Warden attached is refused on its next upload.
    """
    keys = {access.signer_id: access.signer.public_key_bytes}
    uplink = await deps.uplink_keys(link) if deps.uplink_keys is not None else None
    if uplink is not None and link.node_id is not None:
        keys[str(link.node_id)] = uplink
    return Ed25519Verifier(keys)


def _forget(deps: QueenDeps, key: tuple[WardenId, str]) -> None:
    """Drop a finished (or failed) upload's receiver and offer from the lane."""
    deps.tool_lane.receipts.pop(key, None)
    deps.tool_lane.offers.pop(key, None)
