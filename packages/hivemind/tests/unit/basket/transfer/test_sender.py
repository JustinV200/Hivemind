"""Tests for hivemind.basket.transfer.sender: offering, chunking from the receiver's offset.

Fits into the Hive:
    Mirrors src/hivemind/basket/transfer/sender.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.transfer.sender for the module under test.
"""

from __future__ import annotations

import hashlib

import pytest
from builders.basket import make_blob, make_manifest

from hivemind.basket import BlobIntegrityError, TransferError
from hivemind.basket.transfer import (
    CHUNK_BYTES,
    Ack,
    BytesChunkSource,
    RefusalCode,
    Refuse,
    TransferPhase,
    TransferSender,
)
from waggle.clock import FakeClock
from waggle.ids import new_cell_id

_DATA = bytes(range(256)) * ((2 * CHUNK_BYTES + 100) // 256 + 1)
_DATA = _DATA[: 2 * CHUNK_BYTES + 100]  # Three chunks: two full and a 100-byte tail.
_SHA = hashlib.sha256(_DATA).hexdigest()  # The offered blob's hash.


def _offered() -> tuple[TransferSender, str]:
    blob = make_blob(_DATA)
    sender = TransferSender(BytesChunkSource(_DATA))
    sender.offer(make_manifest(blob), new_cell_id(FakeClock()), "test")
    return sender, blob.sha256


def test_offering_makes_an_offer_and_moves_to_offered() -> None:
    blob = make_blob(_DATA)
    sender = TransferSender(BytesChunkSource(_DATA))

    offer = sender.offer(make_manifest(blob), new_cell_id(FakeClock()), "why")

    assert offer.sha256 == blob.sha256 and offer.reason == "why"
    assert sender.phase is TransferPhase.OFFERED and not sender.done


def test_a_second_offer_is_refused() -> None:
    sender, _ = _offered()

    with pytest.raises(TransferError):
        sender.offer(make_manifest(make_blob(_DATA)), new_cell_id(FakeClock()))


def test_a_full_transfer_is_three_chunks_each_cut_from_the_acks_offset() -> None:
    sender, sha256 = _offered()
    received = 0
    sent: list[tuple[int, int, bool]] = []

    # The receiver acks each chunk with the bytes it holds; the sender cuts the next from there.
    ack: Ack | None = Ack(sha256=sha256, received_bytes=0)
    while ack is not None:
        chunks = sender.on_ack(ack)
        if chunks is None:
            break
        [chunk] = chunks
        sent.append((chunk.offset, len(chunk.data), chunk.is_last))
        assert chunk.data == _DATA[chunk.offset : chunk.offset + len(chunk.data)]
        received = chunk.offset + len(chunk.data)
        ack = Ack(sha256=sha256, received_bytes=received, complete=received == len(_DATA))

    assert sent == [
        (0, CHUNK_BYTES, False),
        (CHUNK_BYTES, CHUNK_BYTES, False),
        (2 * CHUNK_BYTES, 100, True),
    ]
    assert sender.done and sender.phase is TransferPhase.COMPLETE


def test_a_resumed_transfer_starts_at_whatever_offset_the_receiver_names() -> None:
    sender, sha256 = _offered()

    # A lost ack, or a receiver restarted with a partial file: it says it holds 1000 bytes.
    [chunk] = sender.on_ack(Ack(sha256=sha256, received_bytes=1_000)) or ()

    assert chunk.offset == 1_000 and len(chunk.data) == CHUNK_BYTES and not chunk.is_last


def test_a_window_returns_that_many_chunks_per_ack() -> None:
    blob = make_blob(_DATA)
    sender = TransferSender(BytesChunkSource(_DATA), window=2)
    sender.offer(make_manifest(blob), new_cell_id(FakeClock()))

    chunks = sender.on_ack(Ack(sha256=blob.sha256, received_bytes=0))

    assert chunks is not None and [c.offset for c in chunks] == [0, CHUNK_BYTES]


def test_an_ack_with_everything_received_but_not_complete_returns_no_chunks() -> None:
    sender, sha256 = _offered()

    assert sender.on_ack(Ack(sha256=sha256, received_bytes=len(_DATA))) == ()


def test_a_repeated_final_ack_is_harmless() -> None:
    sender, sha256 = _offered()
    final = Ack(sha256=sha256, received_bytes=len(_DATA), complete=True)

    assert sender.on_ack(final) is None and sender.on_ack(final) is None
    assert sender.done


def test_a_refusal_ends_the_transfer_and_is_kept() -> None:
    sender, sha256 = _offered()
    refuse = Refuse(sha256=sha256, code=RefusalCode.NO_ROOM, reason="full")

    sender.on_refuse(refuse)

    assert sender.phase is TransferPhase.REFUSED and sender.refusal == refuse and not sender.done
    with pytest.raises(TransferError) as raised:
        sender.on_ack(Ack(sha256=sha256, received_bytes=0))
    assert raised.value.reason_code == "complete"


@pytest.mark.parametrize(
    ("ack", "code"),
    [
        (Ack(sha256="b" * 64, received_bytes=0), "unknown"),
        (Ack(sha256=_SHA, received_bytes=len(_DATA) + 1), "offset"),
        (Ack(sha256=_SHA, received_bytes=5, complete=True), "offset"),
    ],
    ids=["other blob", "past the end", "complete but short"],
)
def test_a_bad_ack_is_a_transfer_error(ack: Ack, code: str) -> None:
    sender, _ = _offered()

    with pytest.raises(TransferError) as raised:
        sender.on_ack(ack)

    assert raised.value.reason_code == code


def test_input_before_any_offer_is_unknown() -> None:
    sender = TransferSender(BytesChunkSource(_DATA))

    with pytest.raises(TransferError) as raised:
        sender.on_ack(Ack(sha256="a" * 64, received_bytes=0))

    assert raised.value.reason_code == "unknown"


def test_a_source_that_returns_a_short_read_is_an_integrity_error() -> None:
    blob = make_blob(_DATA)
    sender = TransferSender(BytesChunkSource(_DATA[:10]))
    sender.offer(make_manifest(blob), new_cell_id(FakeClock()))

    with pytest.raises(BlobIntegrityError):
        sender.on_ack(Ack(sha256=blob.sha256, received_bytes=0))
