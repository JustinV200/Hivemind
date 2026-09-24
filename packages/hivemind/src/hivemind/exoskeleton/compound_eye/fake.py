"""Define FakeScreen and FakeCompoundEye: a display that exists only in memory, for tests and demos.

`FakeScreen` is a display's pixels, described as a background colour and a stack of painted
rectangles, which a test or a fake scenario paints to simulate the UI responding to input (a login
button turning into a welcome banner). `FakeCompoundEye` sees it exactly as the X11 backend sees a
real display: `capture` returns a real, decodable PNG Frame of the captured area's size (one
colour, the one at its top-left; no fake needs more to be seen) and refuses a region that leaves
the screen, and
`region_digest` is a pure function of the region's pixels, computed as run-length rows so the same
pixels always give the same digest however they were painted. `FakeScreen.gone` simulates a
display that vanished, which every call then reports as a PeripheralError.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.compound_eye`. Used by unit tests, the contract suite, the fake
    Exoskeleton scenarios and the flight recorder's tests. Calls into
    `hivemind.exoskeleton.errors`, `.frames` and `.geometry`, and waggle's clock, only.

Key invariants:
    - `region_digest` depends only on the region's pixels: painting a rectangle the colour it
      already has, or anything outside the region, leaves the digest unchanged.
    - Owns mutable state (codingrules 8.5): the painted rectangles and `gone`, changed only
      through `paint`, `clear` and `vanish`.

See Also:
    - hivemind.exoskeleton.compound_eye.x11 for the real backend this imitates.
"""

from __future__ import annotations

import hashlib
import itertools

from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.frames import Frame, solid_png
from hivemind.exoskeleton.geometry import Point, Region, ScreenSize
from waggle.clock import Clock

Rgb = tuple[int, int, int]  # One pixel's colour, 0-255 per channel.
WHITE: Rgb = (255, 255, 255)
_PERIPHERAL = "compound_eye"  # How the fake names itself, as the real backend does.

__all__ = ["WHITE", "FakeCompoundEye", "FakeScreen", "Rgb"]


class FakeScreen:
    """A display's pixels in memory: a background and rectangles painted over it in order."""

    def __init__(self, size: ScreenSize, background: Rgb = WHITE) -> None:
        """Build a blank screen.

        Args:
            size: The screen's size in pixels.
            background: The colour of every pixel nothing has painted.
        """
        self.size = size
        self._background = background
        self._painted: list[tuple[Region, Rgb]] = []
        self.gone = False

    def paint(self, region: Region, colour: Rgb) -> None:
        """Paint `region` one colour, over whatever was there."""
        self._painted.append((region, colour))

    def clear(self) -> None:
        """Return every pixel to the background colour."""
        self._painted.clear()

    def vanish(self) -> None:
        """Simulate the display disappearing: every later capture fails."""
        self.gone = True

    def colour_at(self, point: Point) -> Rgb:
        """Return the colour of one pixel: the last rectangle painted over it, or the background."""
        for region, colour in reversed(self._painted):
            if _contains(region, point):
                return colour
        return self._background

    def row_runs(self, region: Region, y: int) -> tuple[tuple[int, Rgb], ...]:
        """Return one row of `region` as (length, colour) runs: its pixels, compressed."""
        edges = {region.x, region.x + region.width}
        for painted, _ in self._painted:
            if painted.y <= y < painted.y + painted.height:
                edges.update((_clip(painted.x, region), _clip(painted.x + painted.width, region)))
        cuts = sorted(edges)
        runs: list[tuple[int, Rgb]] = []
        # Each span between two cuts is one colour; merging equal neighbours makes the result
        # depend on the pixels alone, not on where rectangles happened to start and end.
        for start, end in itertools.pairwise(cuts):
            colour = self.colour_at(Point(start, y))
            if runs and runs[-1][1] == colour:
                runs[-1] = (runs[-1][0] + end - start, colour)
            else:
                runs.append((end - start, colour))
        return tuple(runs)


class FakeCompoundEye:
    """See a FakeScreen the way the X11 backend sees a real display."""

    def __init__(self, screen: FakeScreen, clock: Clock) -> None:
        """Build the fake eye over `screen`, stamping frames with `clock`."""
        self._screen = screen
        self._clock = clock

    @property
    def screen(self) -> ScreenSize:
        """The fake display's size in pixels."""
        return self._screen.size

    async def capture(self, region: Region | None = None) -> Frame:
        """Capture a one-colour PNG of the screen or `region`; see CompoundEye."""
        self._check(region, "capture")
        area = region or Region(x=0, y=0, width=self.screen.width, height=self.screen.height)
        colour = self._screen.colour_at(Point(area.x, area.y))
        return Frame.from_png(solid_png(area.width, area.height, colour), self._clock.now())

    async def region_digest(self, region: Region) -> str:
        """Return a sha256 of `region`'s pixels, as run-length rows; see CompoundEye."""
        self._check(region, "digest")
        digest = hashlib.sha256(f"{region.width}x{region.height}".encode())
        for y in range(region.y, region.y + region.height):
            for length, colour in self._screen.row_runs(region, y):
                digest.update(length.to_bytes(4, "big") + bytes(colour))
        return digest.hexdigest()

    def _check(self, region: Region | None, operation: str) -> None:
        """Fail as the real backend does: a vanished display, or a region off the screen."""
        if self._screen.gone:
            raise PeripheralError(_PERIPHERAL, operation, "the display is gone")
        if region is not None and not region.fits(self.screen):
            size = self.screen
            reason = f"region {region.spec()} does not fit the {size.width}x{size.height} screen"
            raise PeripheralError(_PERIPHERAL, "crop", reason)


def _contains(region: Region, point: Point) -> bool:
    """Return whether `point` lies inside `region`."""
    return (
        region.x <= point.x < region.x + region.width
        and region.y <= point.y < region.y + region.height
    )


def _clip(x: int, region: Region) -> int:
    """Clamp a column to `region`'s own horizontal span."""
    return max(region.x, min(x, region.x + region.width))
