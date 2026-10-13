"""Tests for hivemind.basket.transfer.models: bounds, the frame-fit rule and the value shapes.

Fits into the Hive:
    Mirrors src/hivemind/basket/transfer/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.basket.transfer.models for the module under test.
"""

from __future__ import annotations

import pytest
from builders.basket import make_blob, make_manifest
from pydantic import ValidationError

from hivemind.basket.transfer import CHUNK_BYTES, Ack, Chunk, Offer, RefusalCode, Refuse
from waggle.clock import FakeClock
from waggle.codec import MAX_FRAME_BYTES
from waggle.ids import new_cell_id

_SHA = "a" * 64


def test_a_base64_chunk_with_its_envelope_always_fits_one_waggle_frame() -> None:
    # base64 inflates by 4/3; 4 KiB covers the envelope and the chunk's other fields.
    assert CHUNK_BYTES * 4 / 3 + 4096 < MAX_FRAME_BYTES


def test_the_chunk_size_is_the_design_value() -> None:
    assert CHUNK_BYTES == 262_144


def test_the_refusal_codes_are_the_designs_seven() -> None:
    assert {code.name for code in RefusalCode} == {
        "CLEARANCE",
        "TAINTED",
        "NO_ROOM",
        "UNKNOWN_SIGNER",
        "INTEGRITY",
        "PLATFORM",
        "UNKNOWN_BLOB",
    }


def test_a_chunk_holds_one_to_chunk_bytes_and_never_prints_them() -> None:
    chunk = Chunk(sha256=_SHA, offset=0, data=b"x" * CHUNK_BYTES, is_last=False)

    assert len(chunk.data) == CHUNK_BYTES and "xxxx" not in repr(chunk)
    with pytest.raises(ValidationError):
        Chunk(sha256=_SHA, offset=0, data=b"x" * (CHUNK_BYTES + 1), is_last=False)
    with pytest.raises(ValidationError):
        Chunk(sha256=_SHA, offset=0, data=b"", is_last=True)


@pytest.mark.parametrize("offset", [-1])
def test_a_chunk_offset_cannot_be_negative(offset: int) -> None:
    with pytest.raises(ValidationError):
        Chunk(sha256=_SHA, offset=offset, data=b"x", is_last=True)


def test_an_offer_exposes_the_manifests_hash_and_size_and_round_trips() -> None:
    blob = make_blob(b"offered bytes")
    offer = Offer(
        manifest=make_manifest(blob), destination_cell_id=new_cell_id(FakeClock()), reason="r"
    )

    assert (offer.sha256, offer.size_bytes) == (blob.sha256, blob.size_bytes)
    assert Offer.model_validate_json(offer.model_dump_json()) == offer


def test_ack_and_refuse_reject_a_malformed_hash_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Ack(sha256="nope", received_bytes=0)
    with pytest.raises(ValidationError):
        Refuse(sha256=_SHA, code=RefusalCode.NO_ROOM, reason="r", extra=1)
