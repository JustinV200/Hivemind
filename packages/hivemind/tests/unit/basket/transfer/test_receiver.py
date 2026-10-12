"""Tests for hivemind.basket.transfer.receiver, and the sender and receiver working together.

Covers a whole transfer in three chunks, resuming after a lost ack and after a restart, an
out-of-order chunk, a tampered last chunk, and every by-rule refusal before the first chunk.

Fits into the Hive:
    Mirrors src/hivemind/basket/transfer/receiver.py and spool.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.transfer.receiver for the module under test.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.basket import make_blob, make_signer, open_test_basket_store

from hivemind.basket import (
    BlobManifest,
    BlobState,
    ResidencyDecision,
    ResidentBasket,
    TransferError,
)
from hivemind.basket.transfer import (
    CHUNK_BYTES,
    Ack,
    BytesChunkSource,
    Chunk,
    Offer,
    RefusalCode,
    Refuse,
    TransferPhase,
    TransferReceiver,
    TransferSender,
)
from hivemind.cell import HoneyClearance, OsFamily
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.signing import Ed25519Verifier

_DATA = (bytes(range(256)) * 4_000)[: 2 * CHUNK_BYTES + 100]  # Three chunks.


class _Planner:
    """A residency planner with a fixed answer."""

    def __init__(self, admit: bool = True) -> None:
        self._admit = admit

    async def plan(self, size_bytes: int) -> ResidencyDecision:
        return ResidencyDecision(self._admit, (), "ok" if self._admit else "no room left")


@dataclass
class _Rig:
    """One transfer's two ends and the keys that tie them."""

    sender: TransferSender
    receiver: TransferReceiver
    offer: Offer
    verifier: Ed25519Verifier
    spool: Path

    async def accept(self, **kwargs: object) -> Ack | Refuse:
        """Run `on_offer` with permissive defaults, any of which a test may override."""
        args: dict[str, object] = {
            "verifier": self.verifier,
            "ceiling": HoneyClearance.C2,
            "resident": _Planner(),
            "platform": OsFamily.LINUX,
        }
        args.update(kwargs)
        return await self.receiver.on_offer(self.offer, **args)  # type: ignore[arg-type]


def _rig(tmp_path: Path, data: bytes = _DATA, **blob_overrides: object) -> _Rig:
    signer, signer_id, verifier = make_signer()
    blob = make_blob(data, **blob_overrides)
    manifest = BlobManifest.sign(blob, signer_id, signer)
    sender = TransferSender(BytesChunkSource(data))
    offer = sender.offer(manifest, new_cell_id(FakeClock()), "test")
    spool = tmp_path / "spool"
    return _Rig(sender, TransferReceiver(spool), offer, verifier, spool)


def _chunk(rig: _Rig, offset: int, length: int, *, data: bytes = _DATA) -> Chunk:
    piece = data[offset : offset + length]
    return Chunk(
        sha256=rig.offer.sha256, offset=offset, data=piece, is_last=offset + length >= len(data)
    )


# ──────────────────────────────────────────────────────────────────────────────
# A whole transfer
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_blob_goes_from_sender_to_receiver_in_three_chunks(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    reply = await rig.accept()
    chunks_sent = 0

    # The sender and receiver drive each other with nothing between them but the values.
    while True:
        assert isinstance(reply, Ack)
        chunks = rig.sender.on_ack(reply)
        if chunks is None:
            break
        [chunk] = chunks
        chunks_sent += 1
        reply = await rig.receiver.on_chunk(chunk)

    assert chunks_sent == 3 and rig.sender.done
    assert rig.receiver.phase is TransferPhase.COMPLETE
    path = rig.receiver.completed_path
    assert path is not None and path.read_bytes() == _DATA and path.name == rig.offer.sha256
    assert not list(rig.spool.glob("*.part"))
    assert rig.receiver.integrity_failures == ()


async def test_the_spool_file_and_directory_are_owner_only(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    await rig.accept()

    await rig.receiver.on_chunk(_chunk(rig, 0, CHUNK_BYTES))

    [part] = rig.spool.glob("*.part")
    assert part.stat().st_mode & 0o077 == 0 and rig.spool.stat().st_mode & 0o077 == 0


# ──────────────────────────────────────────────────────────────────────────────
# Resumability and ordering
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_repeated_chunk_after_a_lost_ack_is_acked_and_not_appended_twice(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    await rig.accept()
    first = _chunk(rig, 0, CHUNK_BYTES)
    await rig.receiver.on_chunk(first)

    repeat = await rig.receiver.on_chunk(first)

    assert isinstance(repeat, Ack) and repeat.received_bytes == CHUNK_BYTES and not repeat.complete
    [part] = rig.spool.glob("*.part")
    assert part.stat().st_size == CHUNK_BYTES


async def test_the_lost_ack_path_through_the_sender_resumes_at_the_receivers_offset(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    reply = await rig.accept()
    assert isinstance(reply, Ack)
    [first] = rig.sender.on_ack(reply) or ()
    await rig.receiver.on_chunk(first)  # Delivered, but its ack is lost on the way back.

    # The sender times out and re-offers; the receiver answers with where it really is.
    again = await rig.accept()

    assert isinstance(again, Ack) and again.received_bytes == CHUNK_BYTES
    [second] = rig.sender.on_ack(again) or ()
    assert second.offset == CHUNK_BYTES


async def test_a_new_receiver_resumes_from_the_partial_file_an_earlier_one_left(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    await rig.accept()
    await rig.receiver.on_chunk(_chunk(rig, 0, CHUNK_BYTES))

    # The process restarted: a fresh receiver, the same spool directory.
    restarted = TransferReceiver(rig.spool)
    reply = await restarted.on_offer(
        rig.offer, rig.verifier, HoneyClearance.C2, _Planner(), OsFamily.LINUX
    )
    assert isinstance(reply, Ack) and reply.received_bytes == CHUNK_BYTES
    await restarted.on_chunk(_chunk(rig, CHUNK_BYTES, CHUNK_BYTES))
    done = await restarted.on_chunk(_chunk(rig, 2 * CHUNK_BYTES, 100))

    assert isinstance(done, Ack) and done.complete
    assert restarted.completed_path is not None
    assert restarted.completed_path.read_bytes() == _DATA


async def test_a_partial_file_longer_than_the_blob_is_discarded_not_resumed(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    rig.spool.mkdir()
    (rig.spool / f"{rig.offer.sha256}.part").write_bytes(b"x" * (len(_DATA) + 5))

    reply = await rig.accept()

    assert isinstance(reply, Ack) and reply.received_bytes == 0
    assert not list(rig.spool.glob("*.part"))


async def test_an_out_of_order_chunk_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    await rig.accept()

    with pytest.raises(TransferError) as raised:
        await rig.receiver.on_chunk(_chunk(rig, CHUNK_BYTES, CHUNK_BYTES))  # Skips the first.

    assert raised.value.reason_code == "offset"
    assert rig.receiver.received_bytes == 0 and not list(rig.spool.glob("*.part"))


async def test_a_chunk_that_overlaps_what_is_held_is_an_offset_error(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    await rig.accept()
    await rig.receiver.on_chunk(_chunk(rig, 0, 1_000))

    with pytest.raises(TransferError) as raised:
        await rig.receiver.on_chunk(_chunk(rig, 500, 1_000))  # Starts inside, ends outside.

    assert raised.value.reason_code == "offset"


async def test_a_chunk_that_runs_past_the_blobs_size_is_too_large(tmp_path: Path) -> None:
    rig = _rig(tmp_path, b"short blob")
    await rig.accept()
    extra = Chunk(sha256=rig.offer.sha256, offset=0, data=b"x" * 50, is_last=True)

    with pytest.raises(TransferError) as raised:
        await rig.receiver.on_chunk(extra)

    assert raised.value.reason_code == "too_large"


async def test_a_chunk_before_any_offer_or_for_another_blob_is_unknown(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    stray = Chunk(sha256="9" * 64, offset=0, data=b"x", is_last=True)

    with pytest.raises(TransferError) as before:
        await rig.receiver.on_chunk(_chunk(rig, 0, 10))
    await rig.accept()
    with pytest.raises(TransferError) as other:
        await rig.receiver.on_chunk(stray)

    assert before.value.reason_code == "unknown" and other.value.reason_code == "unknown"


async def test_a_repeated_final_chunk_after_completion_is_re_acked_complete(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path, b"tiny")
    await rig.accept()
    last = _chunk(rig, 0, 4, data=b"tiny")
    first = await rig.receiver.on_chunk(last)

    # The final ack was lost, so the sender resends the last chunk: still a clean "complete".
    again = await rig.receiver.on_chunk(last)

    assert isinstance(first, Ack) and first.complete
    assert isinstance(again, Ack) and again.complete and again.received_bytes == 4


async def test_an_offer_after_the_transfer_ended_is_a_complete_error(tmp_path: Path) -> None:
    rig = _rig(tmp_path, b"tiny")
    await rig.accept()
    await rig.receiver.on_chunk(_chunk(rig, 0, 4, data=b"tiny"))

    with pytest.raises(TransferError) as raised:
        await rig.accept()

    assert raised.value.reason_code == "complete"


# ──────────────────────────────────────────────────────────────────────────────
# Integrity
# ──────────────────────────────────────────────────────────────────────────────


async def test_a_tampered_last_chunk_is_refused_as_integrity_and_the_bytes_are_deleted(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    await rig.accept()
    await rig.receiver.on_chunk(_chunk(rig, 0, CHUNK_BYTES))
    await rig.receiver.on_chunk(_chunk(rig, CHUNK_BYTES, CHUNK_BYTES))
    tail = _chunk(rig, 2 * CHUNK_BYTES, 100)
    tampered = tail.model_copy(update={"data": b"\x00" * 100})

    reply = await rig.receiver.on_chunk(tampered)

    assert isinstance(reply, Refuse) and reply.code is RefusalCode.INTEGRITY
    assert rig.receiver.phase is TransferPhase.REFUSED and rig.receiver.completed_path is None
    [failure] = rig.receiver.integrity_failures
    assert failure.stage == "whole" and failure.sha256 == rig.offer.sha256
    assert failure.received_bytes == len(_DATA)
    assert list(rig.spool.iterdir()) == []  # Not even a partial file survives.


async def test_a_last_chunk_that_leaves_the_blob_short_is_an_integrity_failure(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    await rig.accept()
    early = Chunk(sha256=rig.offer.sha256, offset=0, data=b"x" * 10, is_last=True)

    reply = await rig.receiver.on_chunk(early)

    assert isinstance(reply, Refuse) and reply.code is RefusalCode.INTEGRITY
    assert list(rig.spool.iterdir()) == []


async def test_nothing_more_is_taken_after_an_integrity_refusal(tmp_path: Path) -> None:
    rig = _rig(tmp_path, b"tiny")
    await rig.accept()
    await rig.receiver.on_chunk(
        Chunk(sha256=rig.offer.sha256, offset=0, data=b"evil", is_last=True)
    )

    with pytest.raises(TransferError) as raised:
        await rig.receiver.on_chunk(_chunk(rig, 0, 4, data=b"tiny"))

    assert raised.value.reason_code == "complete"


# ──────────────────────────────────────────────────────────────────────────────
# Every by-rule refusal happens before the first chunk
# ──────────────────────────────────────────────────────────────────────────────


async def _assert_refused_before_any_byte(
    rig: _Rig, reply: Ack | Refuse, code: RefusalCode
) -> None:
    assert isinstance(reply, Refuse) and reply.code is code
    assert reply.sha256 == rig.offer.sha256 and rig.receiver.phase is TransferPhase.REFUSED
    assert not rig.spool.exists() or list(rig.spool.iterdir()) == []
    with pytest.raises(TransferError):
        await rig.receiver.on_chunk(_chunk(rig, 0, 10))


async def test_a_clearance_above_the_cells_ceiling_is_refused(tmp_path: Path) -> None:
    rig = _rig(tmp_path, clearance=HoneyClearance.C2)

    reply = await rig.accept(ceiling=HoneyClearance.C1)  # A Night Veil Cell.

    await _assert_refused_before_any_byte(rig, reply, RefusalCode.CLEARANCE)


async def test_a_tainted_blob_is_refused(tmp_path: Path) -> None:
    rig = _rig(tmp_path, tainted=True)

    await _assert_refused_before_any_byte(rig, await rig.accept(), RefusalCode.TAINTED)


async def test_a_blob_for_another_platform_is_refused(tmp_path: Path) -> None:
    rig = _rig(tmp_path, platform_tags=(OsFamily.WINDOWS,))

    reply = await rig.accept(platform=OsFamily.LINUX)

    await _assert_refused_before_any_byte(rig, reply, RefusalCode.PLATFORM)


async def test_a_blob_with_no_room_in_the_resident_ceiling_is_refused(tmp_path: Path) -> None:
    rig = _rig(tmp_path)

    reply = await rig.accept(resident=_Planner(admit=False))

    await _assert_refused_before_any_byte(rig, reply, RefusalCode.NO_ROOM)


async def test_a_signer_the_receiver_does_not_trust_is_refused(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    _, _, strangers = make_signer()

    reply = await rig.accept(verifier=strangers)

    await _assert_refused_before_any_byte(rig, reply, RefusalCode.UNKNOWN_SIGNER)
    assert rig.receiver.integrity_failures == ()  # Unattributable is not tampering.


async def test_a_manifest_that_does_not_verify_is_refused_and_recorded(tmp_path: Path) -> None:
    rig = _rig(tmp_path)
    forged_blob = rig.offer.manifest.blob.model_copy(update={"clearance": HoneyClearance.C1})
    rig.offer = rig.offer.model_copy(
        update={"manifest": rig.offer.manifest.model_copy(update={"blob": forged_blob})}
    )

    reply = await rig.accept()

    await _assert_refused_before_any_byte(rig, reply, RefusalCode.INTEGRITY)
    [failure] = rig.receiver.integrity_failures
    assert failure.stage == "manifest" and failure.received_bytes == 0


async def test_a_retired_blob_reads_as_unknown_to_the_receiver(tmp_path: Path) -> None:
    rig = _rig(tmp_path, state=BlobState.RETIRED)

    await _assert_refused_before_any_byte(rig, await rig.accept(), RefusalCode.UNKNOWN_BLOB)


async def test_an_offer_for_a_different_blob_than_the_receiver_handles_is_unknown(
    tmp_path: Path,
) -> None:
    rig = _rig(tmp_path)
    await rig.accept()
    other = _rig(tmp_path, b"another blob entirely")

    with pytest.raises(TransferError) as raised:
        await rig.receiver.on_offer(
            other.offer, other.verifier, HoneyClearance.C2, _Planner(), OsFamily.LINUX
        )

    assert raised.value.reason_code == "unknown"


async def test_a_resident_basket_can_serve_as_the_receivers_residency_planner(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    store = await open_test_basket_store(tmp_path, clock=clock)
    tiny = ResidentBasket(store, new_cell_id(clock), ceiling_bytes=10, clock=clock)
    rig = _rig(tmp_path)

    reply = await rig.accept(resident=tiny)

    await _assert_refused_before_any_byte(rig, reply, RefusalCode.NO_ROOM)
