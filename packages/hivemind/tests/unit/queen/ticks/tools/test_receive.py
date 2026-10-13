"""Tests for hivemind.queen.ticks.tools.transfer.receive: a Warden ships a blob up to the Queen.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/tools/transfer/receive.py (codingrules section 3). The test
    plays the Warden's side with the real `TransferSender`: it offers a signed manifest, answers
    each ack with the next chunk, and reads the Queen's replies off the rig Warden's inbox; the
    real Basket re-deposits what verifies.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.tools.transfer.receive for the module under test.
    - builders.queen_tools for the rig.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from builders.basket import INJECTION, make_blob
from builders.queen_tools import ToolRig, make_tool_rig

from hivemind.basket import (
    BlobManifest,
    BlobNotFoundError,
    MediaKind,
    TransferSender,
    ack_from_wire,
    chunk_to_wire,
    offer_to_wire,
)
from hivemind.basket.transfer import BytesChunkSource, Chunk
from hivemind.cell import HoneyClearance, hive_stand_cell_id
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.ticks.tools.transfer import receive
from waggle.ids import new_node_id
from waggle.messages.basket import BasketAck, BasketRefusal, BasketRefuse
from waggle.messages.control.protocol import ErrorMessage
from waggle.signing import Ed25519Signer

_TWO_CHUNKS = bytes(range(256)) * 1_200  # 307_200 bytes: a full 256 KiB chunk and a short one.


def _manifest(rig: ToolRig, content: bytes, **blob: object) -> BlobManifest:
    """Sign a manifest of `content` with the Hive key, as the in-process Warden does."""
    fields: dict[str, object] = {"origin_cell_id": rig.link.cell.id}
    fields.update(blob)
    return BlobManifest.sign(make_blob(content, **fields), rig.kit.signer_id, rig.kit.wiring.signer)


async def _upload(
    rig: ToolRig, manifest: BlobManifest, content: bytes
) -> BasketAck | BasketRefuse | ErrorMessage:
    """Offer `manifest`, answer every ack with the next chunk, and return the Queen's last word."""
    sender = TransferSender(BytesChunkSource(content))
    stand = hive_stand_cell_id(rig.deps.identity.node_id)
    await rig.send(offer_to_wire(sender.offer(manifest, stand, "a test upload")))
    while True:
        payload = (await rig.reply()).payload
        if not isinstance(payload, BasketAck):
            assert isinstance(payload, BasketRefuse | ErrorMessage)
            return payload
        chunks = sender.on_ack(ack_from_wire(payload))
        if chunks is None:
            return payload
        for chunk in chunks:
            await rig.send(chunk_to_wire(chunk))


async def _kinds(rig: ToolRig, family: str) -> list[str]:
    return [event.kind for event in await rig.deps.trail.query(TrailQuery(family=family))]


def _spool(tmp_path: Path) -> list[Path]:
    incoming = tmp_path / "basket" / receive.INCOMING_DIR
    return sorted(incoming.iterdir()) if incoming.exists() else []


async def test_a_verified_upload_is_redeposited_with_the_links_own_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    manifest = _manifest(rig, _TWO_CHUNKS, clearance=HoneyClearance.C0)

    last = await _upload(rig, manifest, _TWO_CHUNKS)

    assert isinstance(last, BasketAck) and last.complete
    blob = await rig.kit.store.get(manifest.blob.sha256)
    # The Cell is borrowed (Real), so the Basket's floor raises the declared C0 to C2.
    assert blob.origin_cell_id == rig.link.cell.id and blob.clearance is HoneyClearance.C2
    assert blob.origin_bee == rig.link.warden_id
    assert await rig.kit.store.open_bytes(blob.sha256) == _TWO_CHUNKS
    assert [r.sha256 for r in rig.kit.honey.records] == [blob.sha256]
    assert sorted(await _kinds(rig, "basket")) == ["basket.deposited", "basket.received"]
    lane = rig.deps.tool_lane
    assert lane.receipts == {} and lane.offers == {} and _spool(tmp_path) == []
    await rig.aclose()


async def test_an_upload_signed_by_the_wardens_node_key_is_resigned_by_the_hive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    node_id, key = new_node_id(rig.deps.clock), Ed25519Signer.generate()
    link = dataclasses.replace(rig.link, node_id=node_id)
    rig.wardens[link.warden_id] = link
    rig.deps = dataclasses.replace(rig.deps, uplink_keys=lambda _link: key.public_key_bytes)
    content = b"an artefact a Worker kept on the Warden's machine"
    blob = make_blob(content, origin_cell_id=link.cell.id)
    manifest = BlobManifest.sign(blob, str(node_id), key)

    last = await _upload(rig, manifest, content)

    assert isinstance(last, BasketAck) and last.complete
    stored = await rig.kit.store.manifest(blob.sha256)
    assert stored.signer == rig.kit.signer_id  # The Hive's own signature, not the Warden's.
    await rig.aclose()


async def test_an_upload_signed_by_a_key_the_queen_does_not_know_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    content = b"an artefact signed by a stranger"
    manifest = BlobManifest.sign(
        make_blob(content, origin_cell_id=rig.link.cell.id),
        str(new_node_id(rig.deps.clock)),
        Ed25519Signer.generate(),
    )

    last = await _upload(rig, manifest, content)

    assert isinstance(last, BasketRefuse) and last.code is BasketRefusal.UNKNOWN_SIGNER
    assert await _kinds(rig, "basket") == ["basket.refused"]
    assert rig.deps.tool_lane.receipts == {}
    await rig.aclose()


async def test_a_forged_manifest_signature_is_an_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    content = b"an artefact whose manifest was tampered with"
    manifest = _manifest(rig, content)
    other = _manifest(rig, b"a different blob entirely")
    forged = manifest.model_copy(update={"signature": other.signature})

    last = await _upload(rig, forged, content)

    assert isinstance(last, BasketRefuse) and last.code is BasketRefusal.INTEGRITY
    (event,) = await rig.deps.trail.query(TrailQuery(kind="guard.basket_integrity_failed"))
    assert event.payload["stage"] == "manifest" and event.subject_id == rig.link.warden_id
    await rig.aclose()


async def test_bytes_that_do_not_hash_to_the_offer_are_discarded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    manifest = _manifest(rig, _TWO_CHUNKS)
    corrupted = bytes(reversed(_TWO_CHUNKS))  # Same length, other bytes.

    last = await _upload(rig, manifest, corrupted)

    assert isinstance(last, BasketRefuse) and last.code is BasketRefusal.INTEGRITY
    (event,) = await rig.deps.trail.query(TrailQuery(kind="guard.basket_integrity_failed"))
    assert event.payload["stage"] == "whole" and event.payload["received_bytes"] == len(_TWO_CHUNKS)
    with pytest.raises(BlobNotFoundError):
        await rig.kit.store.get(manifest.blob.sha256)
    assert _spool(tmp_path) == [] and rig.deps.tool_lane.receipts == {}
    await rig.aclose()


async def test_a_tainted_blob_is_refused_before_any_byte_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    content = b"a blob from a quarantined source"

    last = await _upload(rig, _manifest(rig, content, tainted=True), content)

    assert isinstance(last, BasketRefuse) and last.code is BasketRefusal.TAINTED
    assert rig.peer.seen == ["basket.refuse"]  # Only the refusal went back: no ack, no chunk.
    await rig.aclose()


async def test_text_the_scanner_flags_is_refused_at_the_deposit_door(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    manifest = _manifest(rig, INJECTION, media_kind=MediaKind.TEXT, media_type="text/plain")

    last = await _upload(rig, manifest, INJECTION)

    # The verified bytes were refused by the door, so the Warden hears an error, not "complete".
    assert isinstance(last, ErrorMessage) and last.code == receive.DEPOSIT_REFUSED
    assert last.failed_kind == "basket.chunk"
    assert "basket.deposit_refused" in await _kinds(rig, "basket")
    with pytest.raises(BlobNotFoundError):
        await rig.kit.store.get(manifest.blob.sha256)
    assert _spool(tmp_path) == [] and rig.deps.tool_lane.receipts == {}
    await rig.aclose()


async def test_a_chunk_for_no_offer_is_answered_with_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch)
    orphan = Chunk(sha256="e" * 64, offset=0, data=b"orphan", is_last=True)

    await rig.send(chunk_to_wire(orphan))

    error = (await rig.reply()).payload
    assert isinstance(error, ErrorMessage) and error.code == receive.BAD_TRANSFER
    await rig.aclose()


async def test_an_upload_to_a_hive_with_no_basket_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = await make_tool_rig(tmp_path, monkeypatch, lab=False)
    content = b"nowhere to keep this"

    last = await _upload(rig, _manifest(rig, content), content)

    assert isinstance(last, ErrorMessage) and last.code == receive.UNAVAILABLE
    await rig.aclose()
