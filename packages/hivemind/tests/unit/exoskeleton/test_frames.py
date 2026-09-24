"""Unit tests for hivemind.exoskeleton.frames: Frame, png_size and solid_png."""

from __future__ import annotations

import struct
import zlib
from datetime import UTC, datetime

import pytest

from hivemind.exoskeleton.frames import PNG_SIGNATURE, Frame, png_size, solid_png

_AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def test_from_png_reads_the_size_from_the_image_and_digests_it() -> None:
    png = solid_png(7, 3, (10, 20, 30))

    frame = Frame.from_png(png, _AT)

    assert (frame.width, frame.height) == (7, 3)
    assert len(frame.sha256) == 64
    assert frame.captured_at == _AT


def test_a_frame_never_renders_its_image_bytes() -> None:
    frame = Frame.from_png(solid_png(4, 4, (1, 2, 3)), _AT)

    for text in (repr(frame), str(frame)):
        assert "\\x89PNG" not in text
        assert "IDAT" not in text
    assert "4x4" in str(frame)


@pytest.mark.parametrize("data", [b"", b"GIF89a" + bytes(40), PNG_SIGNATURE + bytes(4)])
def test_png_size_refuses_anything_that_is_not_a_png(data: bytes) -> None:
    with pytest.raises(ValueError, match="PNG"):
        png_size(data)


def test_solid_png_is_a_decodable_image_of_one_colour() -> None:
    png = solid_png(2, 2, (255, 0, 0))

    # Walk the chunks: IDAT holds zlib-compressed scanlines of filter byte + RGB triples.
    offset, idat = len(PNG_SIGNATURE), b""
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        kind = png[offset + 4 : offset + 8]
        body = png[offset + 8 : offset + 8 + length]
        crc = struct.unpack(">I", png[offset + 8 + length : offset + 12 + length])[0]
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF
        if kind == b"IDAT":
            idat += body
        offset += 12 + length
    assert zlib.decompress(idat) == (b"\x00" + b"\xff\x00\x00" * 2) * 2


@pytest.mark.parametrize(("width", "height", "rgb"), [(0, 1, (0, 0, 0)), (1, 1, (256, 0, 0))])
def test_solid_png_refuses_an_empty_image_or_a_bad_channel(
    width: int, height: int, rgb: tuple[int, int, int]
) -> None:
    with pytest.raises(ValueError, match="solid_png"):
        solid_png(width, height, rgb)
