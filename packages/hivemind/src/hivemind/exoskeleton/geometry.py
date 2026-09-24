"""Define Point, Region and ScreenSize: where on an Exoskeleton display something is.

The Exoskeleton (the optional display, input, audio and browser attachment of a Cell) speaks in
display pixels: the Antennae (pointer and keyboard) move and click at a `Point`, the CompoundEye
(screen capture) crops a `Region`, and a display has a `ScreenSize`. `Region` also has a text form,
"x,y,width,height", because a REGION_CHANGED postcondition (waggle protocol 1.6) carries its
rectangle in `Postcondition.subject`; `Region.parse` and `Region.spec` convert between the two, and
`Region.crop_geometry` renders ImageMagick's own "WxH+X+Y" for a capture.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`. Used
    by every peripheral protocol and backend, the Capping gate's GUI surface and the Worker tools.
    Calls into pydantic only.

Key invariants:
    - A Region always has a width and height of at least 1, and never negative coordinates; its
      text form round-trips (`Region.parse(region.spec()) == region`).
    - Points and regions are validated at construction, so no backend ever builds a command from
      an out-of-range coordinate.

See Also:
    - waggle.messages.labels for the REGION_CHANGED subject format this module parses.
    - hivemind.exoskeleton.compound_eye for the protocol that crops a Region.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

MAX_PIXEL = 16_384  # Mirrors waggle.messages.capping.gui.MAX_COORDINATE: wider than any display.
_REGION_TEXT = re.compile(r"^(\d{1,5}),(\d{1,5}),(\d{1,5}),(\d{1,5})$")  # "x,y,width,height".

__all__ = ["MAX_PIXEL", "Point", "Region", "ScreenSize"]


@dataclass(frozen=True, slots=True)
class Point:
    """One display pixel: where the pointer moves or clicks."""

    x: int  # Pixels from the left edge; 0 is the first column.
    y: int  # Pixels from the top edge; 0 is the first row.

    def __post_init__(self) -> None:
        """Refuse a coordinate outside 0..MAX_PIXEL, before any command is built from it."""
        if not (0 <= self.x <= MAX_PIXEL and 0 <= self.y <= MAX_PIXEL):
            raise ValueError(f"Point ({self.x}, {self.y}) is outside 0..{MAX_PIXEL}.")


@dataclass(frozen=True, slots=True)
class ScreenSize:
    """A display's size in pixels."""

    width: int  # Columns; at least 1.
    height: int  # Rows; at least 1.

    def contains(self, point: Point) -> bool:
        """Return whether `point` lies on this screen.

        Args:
            point: The pixel to test.

        Returns:
            True when 0 <= x < width and 0 <= y < height.
        """
        return point.x < self.width and point.y < self.height


class Region(BaseModel):
    """A rectangle on a display, in pixels: what a capture crops and a REGION_CHANGED names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    x: int = Field(ge=0, le=MAX_PIXEL, description="Left edge, in pixels.")
    y: int = Field(ge=0, le=MAX_PIXEL, description="Top edge, in pixels.")
    width: int = Field(ge=1, le=MAX_PIXEL, description="Width, in pixels; at least 1.")
    height: int = Field(ge=1, le=MAX_PIXEL, description="Height, in pixels; at least 1.")

    @classmethod
    def parse(cls, text: str) -> Region:
        """Parse the "x,y,width,height" text form a REGION_CHANGED postcondition carries.

        Args:
            text: The rectangle, four non-negative integers joined by commas.

        Returns:
            The Region.

        Raises:
            ValueError: `text` is not four comma-separated integers, or they are out of range.
        """
        match = _REGION_TEXT.match(text)
        if match is None:
            raise ValueError(f"A region is 'x,y,width,height' in pixels, got {text!r}.")
        x, y, width, height = (int(part) for part in match.groups())
        return cls(x=x, y=y, width=width, height=height)

    def spec(self) -> str:
        """Return the "x,y,width,height" text form `parse` reads back.

        Returns:
            For example "10,20,300,40".
        """
        return f"{self.x},{self.y},{self.width},{self.height}"

    def crop_geometry(self) -> str:
        """Return ImageMagick's own crop geometry for this rectangle.

        Returns:
            "WIDTHxHEIGHT+X+Y", for example "300x40+10+20".
        """
        return f"{self.width}x{self.height}+{self.x}+{self.y}"

    def fits(self, screen: ScreenSize) -> bool:
        """Return whether this whole rectangle lies on `screen`.

        Args:
            screen: The display to test against.

        Returns:
            True when the rectangle's far corner is still on the screen.
        """
        return self.x + self.width <= screen.width and self.y + self.height <= screen.height
