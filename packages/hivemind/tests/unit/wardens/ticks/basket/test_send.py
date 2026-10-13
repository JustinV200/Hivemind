"""Tests for hivemind.wardens.ticks.basket.send: a Warden shipping a blob up to the Queen.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/basket/send.py (codingrules section 3). The Queen is
    `builders.wardens.QueenDouble` (a real `TransferReceiver` verifying hash and signature) for
    the end-to-end cases, and the test itself, sending hand-made acknowledgements, for the cases
    about how the sender is driven.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.basket.send for the module under test.
    - tests/unit/basket/transfer for the sender state machine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from builders.basket import make_blob, make_manifest
from builders.wardens_tools import QueenDouble, ToolRig

from hivemind.basket import CHUNK_BYTES, MediaKind
from hivemind.pheromone import TrailQuery
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.ticks.basket import handle_basket_item, ship_up
from waggle.ids import new_message_id
from waggle.messages.basket import BasketAck, BasketChunk, BasketOffer
from waggle.signing import Ed25519Verifier

_BIG = bytes(range(256)) * ((CHUNK_BYTES * 5 // 2) // 256)  # Two and a half chunks.


async def _store(rig: ToolRig, content: bytes, **blob: object) -> str:
    """Put `content` in the Cell's own store, signed by its node key; return its hash."""
    cell = rig.warden._cell
    assert cell is not None
    fields: dict[str, object] = {"origin_cell_id": cell.id, "media_kind": MediaKind.DATASET}
    fields.update(blob)
    made = make_blob(content, **fields)
    manifest = make_manifest(made, signer=rig.cell_signer, signer_id=rig.cell_signer_id)
    stored, _ = await rig.store.deposit(content, made, manifest)
    return stored.sha256


def _queen(rig: ToolRig, tmp_path: Path, *, refuse: bool = False) -> QueenDouble:
    verifier = Ed25519Verifier({rig.cell_signer_id: rig.cell_signer.public_key_bytes})
    return QueenDouble(rig, verifier, tmp_path / "queen-spool", refuse=refuse)


async def test_a_blob_is_offered_chunked_and_acknowledged_until_the_queen_holds_it(
    cell_rig: ToolRig, tmp_path: Path
) -> None:
    sha256 = await _store(cell_rig, _BIG)
    queen = _queen(cell_rig, tmp_path)

    job = asyncio.ensure_future(ship_up(cell_rig.warden, sha256, "kept for later"))
    await queen.serve(job)

    assert await job is True
    assert queen.received[sha256] == _BIG  # Verified by the Queen's real receiver.
    [offer] = queen.offers
    assert offer.destination_cell_id == cell_rig.warden._cell.id  # type: ignore[union-attr]
    assert offer.reason == "kept for later" and not offer.tainted
    kinds = [
        e.kind
        for e in await cell_rig.warden._deps.trail.query(
            TrailQuery(subject_id=str(offer.origin_cell_id))
        )
    ]
    assert "basket.offered" in kinds and "basket.shipped" in kinds
    assert cell_rig.warden._basket.outgoing == {}


async def test_a_refusal_ends_the_shipment_and_is_recorded(
    cell_rig: ToolRig, tmp_path: Path
) -> None:
    sha256 = await _store(cell_rig, b"turned away")
    queen = _queen(cell_rig, tmp_path, refuse=True)

    job = asyncio.ensure_future(ship_up(cell_rig.warden, sha256, "x"))
    await queen.serve(job)

    assert await job is False
    [event] = await cell_rig.warden._deps.trail.query(TrailQuery(kind="basket.refused"))
    assert event.payload["code"] == "CLEARANCE" and event.payload["direction"] == "up"
    assert cell_rig.warden._basket.outgoing == {}


async def test_the_sender_resumes_from_the_byte_the_queens_acknowledgement_names(
    cell_rig: ToolRig,
) -> None:
    sha256 = await _store(cell_rig, _BIG)
    job = asyncio.ensure_future(ship_up(cell_rig.warden, sha256, "resume"))
    offer_env = await cell_rig.queen.next()
    assert isinstance(offer_env.payload, BasketOffer)

    # The Queen already holds the first chunk (an earlier attempt): the ack says so.
    ack = BasketAck(sha256=sha256, received_bytes=CHUNK_BYTES, complete=False, reason="")
    reply = cell_rig.queen.envelope_for(ack, correlation_id=offer_env.id)
    await handle_basket_item(cell_rig.warden, to_inbox_item(reply, QUEEN_LINK))

    chunk_env = await cell_rig.queen.next()
    assert isinstance(chunk_env.payload, BasketChunk)
    assert chunk_env.payload.offset == CHUNK_BYTES  # Not from zero.
    assert not chunk_env.payload.is_last
    job.cancel()
    await asyncio.gather(job, return_exceptions=True)


async def test_a_queen_that_never_answers_ends_the_shipment_after_the_timeout(
    cell_rig: ToolRig,
) -> None:
    sha256 = await _store(cell_rig, b"nobody answers")
    job = asyncio.ensure_future(ship_up(cell_rig.warden, sha256, "x"))
    await cell_rig.queen.next()  # The offer goes out and gets no answer.

    await asyncio.sleep(0)
    cell_rig.clock.advance(601.0)  # Past [royal_jelly] request_timeout_s (600 s).

    assert await job is False
    [event] = await cell_rig.warden._deps.trail.query(TrailQuery(kind="basket.refused"))
    assert event.payload["code"] == "timeout"
    assert cell_rig.warden._basket.outgoing == {}


async def test_a_tainted_blob_is_never_offered(cell_rig: ToolRig) -> None:
    sha256 = await _store(cell_rig, b"from an isolated source", tainted=True)

    assert await ship_up(cell_rig.warden, sha256, "x") is False

    await cell_rig.warden._deps.queen_link.close()
    try:
        await cell_rig.queen.next()
    except StopAsyncIteration:
        return
    raise AssertionError("a tainted blob was offered to the Queen")


async def test_a_blob_the_warden_does_not_hold_cannot_be_shipped(cell_rig: ToolRig) -> None:
    assert await ship_up(cell_rig.warden, "0" * 64, "x") is False


async def test_the_hive_stands_store_is_the_queens_so_shipping_is_already_done(
    hive_rig: ToolRig,
) -> None:
    assert await ship_up(hive_rig.warden, "0" * 64, "x") is True
    await hive_rig.warden._deps.queen_link.close()
    try:
        await hive_rig.queen.next()
    except StopAsyncIteration:
        return
    raise AssertionError("the Hive Stand's Warden sent something up the wire")


async def test_an_answer_for_a_shipment_that_is_not_standing_is_dropped(cell_rig: ToolRig) -> None:
    stray = BasketAck(sha256="1" * 64, received_bytes=0, complete=False, reason="")
    envelope = cell_rig.queen.envelope_for(stray, correlation_id=new_message_id(cell_rig.clock))

    await handle_basket_item(cell_rig.warden, to_inbox_item(envelope, QUEEN_LINK))  # must not raise

    assert cell_rig.warden._basket.outgoing == {}
