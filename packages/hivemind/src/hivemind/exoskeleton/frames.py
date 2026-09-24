"""Define Frame: one captured screen image, and the PNG helpers every peripheral shares.

A `Frame` is what the CompoundEye (screen capture) or the browser fast path hands back: PNG bytes,
their pixel size, a sha256 of the bytes and when they were captured. Screenshots are evidence the
flight recorder keeps and a vision model may see, but they must never reach a log, the Pheromone
Trail (the append-only audit log) or a tool result's text (codingrules section 12), so a Frame's
`repr` and `str` name only its size and digest: an accidental `log.info(frame=frame)` leaks nothing.
`png_size` reads a PNG's width and height from its header without decoding it, and `solid_png`
builds a small single-colour PNG with the standard library alone, which the fake peripherals use to
produce real, decodable frames without an imaging library.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`.
    Produced by `hivemind.exoskeleton.compound_eye` and `.browser` backends, stored by the flight
    recorder (`hivemind.exoskeleton.recorder`), shown to a vision model by the `see` tool. Calls
    into the standard library (`hashlib`, `struct`, `zlib`) and pydantic only.

Key invariants:
    - `Frame.png` always starts with the PNG signature and `width`/`height` match its IHDR chunk
      (`Frame.from_png` is the one constructor that reads them, and it refuses anything else).
    - `repr(frame)` and `str(frame)` never contain the image bytes.

See Also:
    - .claude/codingrules.md section 12 for "never log screenshots".
    - hivemind.exoskeleton.compound_eye for the protocol that captures a Frame.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from waggle.messages.base import UtcDatetime

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"  # The eight bytes every PNG starts with.
PNG_MEDIA_TYPE = "image/png"  # The media type a Frame's bytes are, for an ImagePart.
_IHDR_END = 24  # Signature (8) + chunk length (4) + "IHDR" (4) + width (4) + height (4).
MAX_SOLID_PNG_SIDE = 4_096  # solid_png is for fakes and tests; nothing needs a bigger blank.

__all__ = [
    "MAX_SOLID_PNG_SIDE",
    "PNG_MEDIA_TYPE",
    "PNG_SIGNATURE",
    "Frame",
    "png_size",
    "solid_png",
]


class Frame(BaseModel):
    """One captured screen image: PNG bytes, their size, their digest and when they were taken.

    Built only through `from_png`, which reads the size from the image itself. Its `repr` and
    `str` never include the bytes (module docstring).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    png: bytes = Field(repr=False, description="The image, PNG-encoded; never logged.")
    width: int = Field(ge=1, description="Pixel width, from the PNG header.")
    height: int = Field(ge=1, description="Pixel height, from the PNG header.")
    sha256: str = Field(description="Hex sha256 of `png`, for comparison and the recorder.")
    captured_at: UtcDatetime = Field(description="When the capture completed.")

    @classmethod
    def from_png(cls, png: bytes, captured_at: datetime) -> Frame:
        """Build a Frame from PNG bytes, reading width and height from the image's own header.

        Args:
            png: The encoded image.
            captured_at: When the capture completed, from an injected clock.

        Returns:
            The Frame.

        Raises:
            ValueError: `png` is not a PNG image.
        """
        width, height = png_size(png)
        digest = hashlib.sha256(png).hexdigest()
        return cls(png=png, width=width, height=height, sha256=digest, captured_at=captured_at)

    def __str__(self) -> str:
        """Render as its size and a short digest, never the bytes."""
        return f"Frame({self.width}x{self.height}, sha256={self.sha256[:12]}, {len(self.png)} B)"


def png_size(png: bytes) -> tuple[int, int]:
    """Read a PNG's width and height from its IHDR chunk, without decoding it.

    Args:
        png: The encoded image.

    Returns:
        (width, height) in pixels.

    Raises:
        ValueError: `png` does not start with the PNG signature and an IHDR chunk.
    """
    # Every valid PNG starts with its signature and then the IHDR chunk; anything else (an empty
    # capture, an error page, another format) is refused here rather than stored as a frame.
    if len(png) < _IHDR_END or not png.startswith(PNG_SIGNATURE) or png[12:16] != b"IHDR":
        raise ValueError(f"Not a PNG image ({len(png)} bytes).")
    width, height = struct.unpack(">II", png[16:24])
    if width < 1 or height < 1:
        raise ValueError(f"A PNG header claims a {width}x{height} image.")
    return int(width), int(height)


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Encode a single-colour RGB PNG with the standard library alone.

    For the fake peripherals and tests: a real, decodable image with no imaging library.

    Args:
        width: Pixel width, 1 to MAX_SOLID_PNG_SIDE.
        height: Pixel height, 1 to MAX_SOLID_PNG_SIDE.
        rgb: The colour, three 0-255 channels.

    Returns:
        The PNG bytes.

    Raises:
        ValueError: A side or a channel is out of range.
    """
    if not (1 <= width <= MAX_SOLID_PNG_SIDE and 1 <= height <= MAX_SOLID_PNG_SIDE):
        raise ValueError(f"solid_png sides are 1..{MAX_SOLID_PNG_SIDE}, got {width}x{height}.")
    if not all(0 <= channel <= 255 for channel in rgb):
        raise ValueError(f"solid_png channels are 0..255, got {rgb}.")
    # Each scanline is a filter byte (0: none) followed by the row's RGB triples.
    row = b"\x00" + bytes(rgb) * width
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB, no interlace.
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(row * height))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Frame one PNG chunk: length, type, data, and the CRC over type and data."""
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)
