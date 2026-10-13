"""Tests for the basket family's transfer half (basket.chunk): BasketChunk and BasketAck.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Pins, for BasketChunk and BasketAck, construction,
    the base64 decode check on data, the raw-byte cap at and one past MAX_CHUNK_BYTES, the offset
    and received-bytes floors, and that a full-size chunk still fits one frame. The family's
    EXAMPLES tuple, the round trip and the digest pattern live in test_offer.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.messages.basket.chunk for the module under test.
    - test_offer.py for EXAMPLES and the family's negotiation half.
    - docs/waggle/spec.md section 8.12 for the fields, bounds and validators pinned here.
"""

from __future__ import annotations

import base64
from collections.abc import Callable

import pytest
from pydantic import ValidationError

from waggle.codec import MAX_FRAME_BYTES, Codec
from waggle.envelope import Envelope
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.basket import BasketAck, BasketChunk
from waggle.messages.basket.chunk import MAX_CHUNK_DATA_CHARS

SHA256 = "ef" * 32  # 32 digest bytes as 64 lowercase hex characters.


def _encode(raw: bytes) -> str:
    """Standard padded base64 text of ``raw``, the form BasketChunk.data takes."""
    return base64.b64encode(raw).decode("ascii")


def _chunk(**changes: object) -> BasketChunk:
    """A valid chunk of five bytes with some fields replaced."""
    fields: dict[str, object] = {
        "sha256": SHA256,
        "offset": 0,
        "data": _encode(b"hello"),
        "is_last": False,
    }
    return BasketChunk.model_validate({**fields, **changes})


def test_the_chunk_cap_is_the_normative_256_kib_and_its_text_bound_follows() -> None:
    assert MAX_CHUNK_BYTES == 262_144
    # Four characters per started group of three bytes: 87_382 groups for 262_144 bytes.
    assert MAX_CHUNK_DATA_CHARS == 349_528
    assert len(_encode(b"\x00" * MAX_CHUNK_BYTES)) == MAX_CHUNK_DATA_CHARS


def test_basket_chunk_decodes_back_to_the_bytes_it_was_built_from() -> None:
    chunk = _chunk(data=_encode(bytes(range(256))), offset=4_096, is_last=True)

    assert base64.b64decode(chunk.data) == bytes(range(256))
    assert chunk.offset == 4_096
    assert chunk.is_last is True


@pytest.mark.parametrize("size", [1, 2, 3, 4, 1_000, MAX_CHUNK_BYTES - 1, MAX_CHUNK_BYTES])
def test_basket_chunk_accepts_every_size_up_to_the_cap(size: int) -> None:
    assert _chunk(data=_encode(b"\xab" * size))


@pytest.mark.parametrize("size", [MAX_CHUNK_BYTES + 1, MAX_CHUNK_BYTES + 3])
def test_basket_chunk_refuses_a_chunk_over_the_cap(size: int) -> None:
    # Over the cap by a byte still fits the character bound for sizes that round into the same
    # group, so the raw-byte check is the one that must refuse it; a larger one hits the text cap.
    with pytest.raises(ValidationError, match=r"decode to 1 to 262144 bytes|at most 349528"):
        _chunk(data=_encode(b"\xab" * size))


def test_basket_chunk_refuses_data_far_beyond_the_text_bound_without_decoding_it() -> None:
    with pytest.raises(ValidationError, match=f"at most {MAX_CHUNK_DATA_CHARS}"):
        _chunk(data="A" * (MAX_CHUNK_DATA_CHARS + 4))


@pytest.mark.parametrize(
    "text",
    ["not base64!", "aGVsbG8", "aGVs bG8=", "aGVs\nbG8=", "a-_=", "====", "aGVsbG8==="],
    ids=["punctuation", "unpadded", "space", "newline", "url-safe", "only padding", "over-padded"],
)
def test_basket_chunk_refuses_text_that_is_not_standard_padded_base64(text: str) -> None:
    with pytest.raises(ValidationError, match="not valid standard base64"):
        _chunk(data=text)


def test_basket_chunk_refuses_an_empty_chunk() -> None:
    with pytest.raises(ValidationError, match="decode to 1 to 262144 bytes"):
        _chunk(data="")


def test_basket_chunk_refuses_a_negative_offset_and_accepts_zero() -> None:
    assert _chunk(offset=0)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        _chunk(offset=-1)


def test_basket_chunk_requires_every_field() -> None:
    with pytest.raises(ValidationError, match="is_last"):
        BasketChunk.model_validate({"sha256": SHA256, "offset": 0, "data": _encode(b"x")})


def test_a_full_size_chunk_still_fits_one_frame(
    make_envelope: Callable[..., Envelope], plain_codec: Codec
) -> None:
    chunk = _chunk(data=_encode(b"\xff" * MAX_CHUNK_BYTES), offset=MAX_CHUNK_BYTES, is_last=True)

    frame = plain_codec.encode(make_envelope(chunk))

    assert len(frame) < MAX_FRAME_BYTES


@pytest.mark.parametrize("received", [0, 5, 10**9])
def test_basket_ack_reports_contiguous_bytes_held(received: int) -> None:
    ack = BasketAck(sha256=SHA256, received_bytes=received, complete=False, reason="")

    assert ack.received_bytes == received
    assert ack.complete is False


def test_basket_ack_refuses_negative_received_bytes() -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        BasketAck(sha256=SHA256, received_bytes=-1, complete=False, reason="")
