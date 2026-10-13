"""Tests for hivemind.wardens.ticks.basket.receive: a Cell's Warden receiving a shipped blob.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/basket/receive.py (codingrules section 3). The Queen is the
    test, sending with the real `TransferSender` and a manifest signed by the Hive key the Warden
    trusts; the Warden's replies are read raw off the Queen's end of its link.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.basket.receive for the module under test.
    - tests/unit/basket/transfer for the state machines this drives.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from builders.basket import make_blob, make_manifest, make_signer
from builders.wardens_tools import ToolRig, add_sub_bee, make_tool_rig

from hivemind.basket import (
    CHUNK_BYTES,
    BlobManifest,
    Chunk,
    RefusalCode,
    ResidentBasket,
    TransferSender,
    ack_from_wire,
    chunk_to_wire,
    offer_to_wire,
)
from hivemind.basket.transfer import BytesChunkSource
from hivemind.cell import CombShieldLevel, HoneyClearance, OsFamily
from hivemind.pheromone import TrailQuery
from hivemind.wardens.inbox import to_inbox_item
from hivemind.wardens.links import QUEEN_LINK
from hivemind.wardens.ticks.basket import handle_basket_item
from waggle.envelope import Envelope
from waggle.ids import new_cell_id
from waggle.messages.basket import BasketAck, BasketRefuse

_BIG = bytes(range(256)) * ((CHUNK_BYTES * 5 // 2) // 256)  # Two and a half chunks.


def _manifest(rig: ToolRig, content: bytes, **blob: object) -> BlobManifest:
    """Sign a blob of `content` with the Hive key this rig's Warden trusts."""
    cell = rig.warden._cell
    assert cell is not None
    fields: dict[str, object] = {"origin_cell_id": cell.id}
    fields.update(blob)
    return make_manifest(
        make_blob(content, **fields), signer=rig.hive_signer, signer_id=rig.hive_signer_id
    )


async def _deliver(rig: ToolRig, payload: object) -> Envelope:
    """Hand the Warden a Queen-sent payload and return the reply it sent back."""
    envelope = rig.queen.envelope_for(payload)  # type: ignore[arg-type]
    await handle_basket_item(rig.warden, to_inbox_item(envelope, QUEEN_LINK))
    reply = await rig.queen.next()
    assert reply.correlation_id == envelope.id
    return reply


async def _ship(
    rig: ToolRig, content: bytes, manifest: BlobManifest | None = None
) -> list[Envelope]:
    """Run a whole shipment as the Queen would; return every reply the Warden gave."""
    cell = rig.warden._cell
    assert cell is not None
    sender = TransferSender(BytesChunkSource(content))
    offer = sender.offer(manifest or _manifest(rig, content), cell.id, "test shipment")
    replies = [await _deliver(rig, offer_to_wire(offer))]
    while isinstance(replies[-1].payload, BasketAck):
        chunks = sender.on_ack(ack_from_wire(replies[-1].payload))
        if not chunks:
            break
        replies.append(await _deliver(rig, chunk_to_wire(chunks[0])))
    return replies


async def test_a_shipment_is_acknowledged_chunk_by_chunk_and_kept_resident_once_verified(
    cell_rig: ToolRig,
) -> None:
    replies = await _ship(cell_rig, _BIG)

    acks = [reply.payload for reply in replies if isinstance(reply.payload, BasketAck)]
    assert len(acks) == len(replies)
    assert [ack.received_bytes for ack in acks] == [0, CHUNK_BYTES, 2 * CHUNK_BYTES, len(_BIG)]
    assert [ack.complete for ack in acks] == [False, False, False, True]
    sha256 = acks[0].sha256
    resident = cell_rig.warden._deps.resident_basket
    assert resident is not None and await resident.is_resident(sha256)
    assert await resident.bytes_for(sha256) == _BIG
    [received] = await cell_rig.warden._deps.trail.query(TrailQuery(kind="basket.received"))
    assert received.payload["sha256"] == sha256
    assert cell_rig.warden._basket.receivers == {} and cell_rig.warden._basket.offers == {}
    spool = cell_rig.warden._lease.scratch_root / "basket" / "incoming"  # type: ignore[union-attr]
    assert not any(spool.glob("*"))  # The spool holds nothing once the blob is kept.


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"tainted": True}, RefusalCode.TAINTED),
        ({"platform_tags": (OsFamily.WINDOWS,)}, RefusalCode.PLATFORM),
    ],
)
async def test_an_offer_is_refused_by_rule_before_a_byte_moves(
    cell_rig: ToolRig, overrides: dict[str, object], code: RefusalCode
) -> None:
    content = b"a small blob"

    [reply] = await _ship(cell_rig, content, _manifest(cell_rig, content, **overrides))

    assert isinstance(reply.payload, BasketRefuse)
    assert reply.payload.code.value == code.value
    [event] = await cell_rig.warden._deps.trail.query(TrailQuery(kind="basket.refused"))
    assert event.payload["code"] == code.value
    resident = cell_rig.warden._deps.resident_basket
    assert resident is not None and not await resident.is_resident(reply.payload.sha256)
    assert cell_rig.warden._basket.receivers == {}


async def test_a_night_veil_cell_refuses_a_blob_above_c1(tmp_path: Path) -> None:
    rig = await make_tool_rig(tmp_path, hive_stand=False, comb_shield=CombShieldLevel.NIGHT_VEIL)
    content = b"from a real machine"

    [reply] = await _ship(rig, content, _manifest(rig, content, clearance=HoneyClearance.C2))

    assert isinstance(reply.payload, BasketRefuse)
    assert reply.payload.code.value == "CLEARANCE"
    await rig.warden.stop()


async def test_an_offer_the_resident_ceiling_cannot_fit_is_refused_for_room(
    cell_rig: ToolRig,
) -> None:
    cell = cell_rig.warden._cell
    assert cell is not None
    tiny = ResidentBasket(cell_rig.store, cell.id, 4, cell_rig.clock)
    cell_rig.warden._deps = dataclasses.replace(cell_rig.warden._deps, resident_basket=tiny)

    [reply] = await _ship(cell_rig, b"far more than four bytes")

    assert isinstance(reply.payload, BasketRefuse) and reply.payload.code.value == "NO_ROOM"


async def test_a_manifest_signed_by_a_node_the_warden_does_not_trust_is_refused(
    cell_rig: ToolRig,
) -> None:
    stranger, stranger_id, _ = make_signer()
    content = b"signed by a stranger"
    cell = cell_rig.warden._cell
    assert cell is not None
    manifest = make_manifest(
        make_blob(content, origin_cell_id=cell.id), signer=stranger, signer_id=stranger_id
    )

    [reply] = await _ship(cell_rig, content, manifest)

    assert isinstance(reply.payload, BasketRefuse)
    assert reply.payload.code.value == "UNKNOWN_SIGNER"


async def test_a_forged_manifest_signature_is_an_integrity_failure_on_the_guards_trail(
    cell_rig: ToolRig,
) -> None:
    content = b"signed with the wrong key under the right name"
    forger, _forger_id, _ = make_signer()
    cell = cell_rig.warden._cell
    assert cell is not None
    manifest = make_manifest(
        make_blob(content, origin_cell_id=cell.id),
        signer=forger,
        signer_id=cell_rig.hive_signer_id,
    )

    [reply] = await _ship(cell_rig, content, manifest)

    assert isinstance(reply.payload, BasketRefuse) and reply.payload.code.value == "INTEGRITY"
    [failure] = await cell_rig.warden._deps.trail.query(
        TrailQuery(kind="guard.basket_integrity_failed")
    )
    assert failure.payload["stage"] == "manifest"


async def test_bytes_that_do_not_hash_to_the_offer_are_discarded_and_never_kept(
    cell_rig: ToolRig,
) -> None:
    content = b"A" * 1000
    tampered = b"B" * 1000
    cell = cell_rig.warden._cell
    assert cell is not None
    sender = TransferSender(BytesChunkSource(tampered))  # Cuts chunks of the wrong bytes.
    manifest = _manifest(cell_rig, content)
    offer = sender.offer(manifest, cell.id, "tampered in flight")
    ack = await _deliver(cell_rig, offer_to_wire(offer))
    assert isinstance(ack.payload, BasketAck)

    [chunk] = sender.on_ack(ack_from_wire(ack.payload)) or ()
    reply = await _deliver(cell_rig, chunk_to_wire(chunk))

    assert isinstance(reply.payload, BasketRefuse) and reply.payload.code.value == "INTEGRITY"
    [failure] = await cell_rig.warden._deps.trail.query(
        TrailQuery(kind="guard.basket_integrity_failed")
    )
    assert failure.payload["stage"] == "whole"
    resident = cell_rig.warden._deps.resident_basket
    assert resident is not None and not await resident.is_resident(manifest.blob.sha256)
    assert cell_rig.warden._basket.receivers == {}
    spool = cell_rig.warden._lease.scratch_root / "basket" / "incoming"  # type: ignore[union-attr]
    assert not any(spool.glob("*"))  # Corrupt bytes are never resumed from.


async def test_a_dropped_link_resumes_from_the_bytes_already_held(cell_rig: ToolRig) -> None:
    cell = cell_rig.warden._cell
    assert cell is not None
    manifest = _manifest(cell_rig, _BIG)
    first = TransferSender(BytesChunkSource(_BIG))
    ack = await _deliver(cell_rig, offer_to_wire(first.offer(manifest, cell.id, "first try")))
    assert isinstance(ack.payload, BasketAck)
    [chunk] = first.on_ack(ack_from_wire(ack.payload)) or ()
    await _deliver(cell_rig, chunk_to_wire(chunk))
    # The Warden restarts its side of the transfer: only the spooled bytes survive.
    cell_rig.warden._basket.receivers.clear()
    cell_rig.warden._basket.offers.clear()

    second = TransferSender(BytesChunkSource(_BIG))
    resumed = await _deliver(cell_rig, offer_to_wire(second.offer(manifest, cell.id, "second try")))

    assert isinstance(resumed.payload, BasketAck)
    assert resumed.payload.received_bytes == CHUNK_BYTES  # Not from zero.
    remaining = second.on_ack(ack_from_wire(resumed.payload)) or ()
    assert remaining[0].offset == CHUNK_BYTES
    reply = resumed
    while remaining:
        reply = await _deliver(cell_rig, chunk_to_wire(remaining[0]))
        assert isinstance(reply.payload, BasketAck)
        remaining = second.on_ack(ack_from_wire(reply.payload)) or ()
    assert isinstance(reply.payload, BasketAck) and reply.payload.complete
    resident = cell_rig.warden._deps.resident_basket
    assert resident is not None and await resident.bytes_for(manifest.blob.sha256) == _BIG


async def test_a_chunk_resent_after_a_lost_ack_is_acknowledged_not_appended_twice(
    cell_rig: ToolRig,
) -> None:
    cell = cell_rig.warden._cell
    assert cell is not None
    sender = TransferSender(BytesChunkSource(_BIG))
    ack = await _deliver(
        cell_rig, offer_to_wire(sender.offer(_manifest(cell_rig, _BIG), cell.id, "x"))
    )
    assert isinstance(ack.payload, BasketAck)
    [chunk] = sender.on_ack(ack_from_wire(ack.payload)) or ()

    first = await _deliver(cell_rig, chunk_to_wire(chunk))
    again = await _deliver(cell_rig, chunk_to_wire(chunk))

    assert isinstance(first.payload, BasketAck) and isinstance(again.payload, BasketAck)
    assert first.payload.received_bytes == again.payload.received_bytes == CHUNK_BYTES


async def test_an_offer_for_a_blob_already_resident_is_complete_at_once(cell_rig: ToolRig) -> None:
    content = b"already here"
    await _ship(cell_rig, content)

    [reply] = await _ship(cell_rig, content)

    assert isinstance(reply.payload, BasketAck) and reply.payload.complete


async def test_an_offer_addressed_to_another_cell_is_refused(cell_rig: ToolRig) -> None:
    content = b"for somebody else"
    sender = TransferSender(BytesChunkSource(content))
    offer = sender.offer(_manifest(cell_rig, content), new_cell_id(cell_rig.clock), "misaddressed")

    reply = await _deliver(cell_rig, offer_to_wire(offer))

    assert isinstance(reply.payload, BasketRefuse) and reply.payload.code.value == "UNKNOWN_BLOB"


async def test_a_chunk_with_no_standing_offer_is_refused(cell_rig: ToolRig) -> None:
    stray = Chunk(
        sha256=_manifest(cell_rig, b"never offered").blob.sha256, offset=0, data=b"x", is_last=True
    )

    reply = await _deliver(cell_rig, chunk_to_wire(stray))

    assert isinstance(reply.payload, BasketRefuse) and reply.payload.code.value == "UNKNOWN_BLOB"


async def test_basket_traffic_from_a_sub_bee_is_dropped(cell_rig: ToolRig) -> None:
    sub_bee, worker = await add_sub_bee(cell_rig.warden, cell_rig.clock)
    content = b"smuggled by a worker"
    cell = cell_rig.warden._cell
    assert cell is not None
    offer = TransferSender(BytesChunkSource(content)).offer(
        _manifest(cell_rig, content), cell.id, "x"
    )
    envelope = worker.envelope_for(offer_to_wire(offer))

    await handle_basket_item(cell_rig.warden, to_inbox_item(envelope, sub_bee.worker_id))

    await cell_rig.warden._deps.queen_link.close()
    with pytest.raises(StopAsyncIteration):
        await cell_rig.queen.next()
    assert cell_rig.warden._basket.receivers == {}
