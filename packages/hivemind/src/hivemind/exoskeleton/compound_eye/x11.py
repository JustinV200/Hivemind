"""Define X11CompoundEye: the CompoundEye over an X display, capturing with ImageMagick's import.

The CompoundEye is the Exoskeleton's vision peripheral (`hivemind.exoskeleton.compound_eye.base`).
This backend runs ImageMagick's `import` on the Cell, through its `CellSession`, against one X
display (`hivemind.exoskeleton.x11.X11Display`): PNG on stdout for a frame, whole or cropped, and
raw 8-bit RGB on stdout for a region digest. The raw form is what makes REGION_CHANGED cheap and
exact: two captures of an unchanged rectangle hash to the same value byte for byte, where two PNG
encodings could differ in metadata, and nothing is decoded on either side.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`. Built
    by `hivemind.exoskeleton.attach` for a display it started or was allowed to use; read by the
    `see` tool, the flight recorder and the Capping gate's GUI surface. Calls into
    `hivemind.cell` (CellSession), `hivemind.exoskeleton.commands`, `.errors`, `.frames`,
    `.geometry` and `.x11` only.

Key invariants:
    - Every capture runs with CAPTURE_TIMEOUT_S; a hung X server is a PeripheralError, not a hang.
    - A region that does not fit the screen is refused before any command runs.
    - A digest is computed over exactly width * height * 3 bytes; any other length is an error,
      never a digest of a truncated capture.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the X11 backend choice.
    - images/desktop-ubuntu/README.md for why ImageMagick is installed.
"""

from __future__ import annotations

import hashlib

from hivemind.cell import CellSession
from hivemind.exoskeleton.commands import PeripheralCommand, run_peripheral
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.frames import Frame
from hivemind.exoskeleton.geometry import Region, ScreenSize
from hivemind.exoskeleton.x11 import X11Display
from waggle.clock import Clock

CAPTURE_TIMEOUT_S = 10.0  # A full HD capture takes well under a second; ten is a stuck server.
_PERIPHERAL = "compound_eye"  # How this backend names itself in a PeripheralError.
_RGB_BYTES_PER_PIXEL = 3  # `-depth 8 rgb:-` writes one byte per channel, three channels.

__all__ = ["CAPTURE_TIMEOUT_S", "X11CompoundEye"]


class X11CompoundEye:
    """See one X display on a Cell with ImageMagick's `import`, through the Cell's session."""

    def __init__(self, session: CellSession, display: X11Display, clock: Clock) -> None:
        """Build the eye for `display`.

        Args:
            session: The Cell's session; every capture runs through it.
            display: The X display to capture, with its authority and size.
            clock: Stamps each Frame's `captured_at`.
        """
        self._session = session
        self._display = display
        self._clock = clock

    @property
    def screen(self) -> ScreenSize:
        """The display's size in pixels."""
        return self._display.size

    async def capture(self, region: Region | None = None) -> Frame:
        """Capture the display, or one rectangle of it, as a PNG Frame; see CompoundEye."""
        crop = self._crop_arguments(region) if region is not None else ()
        argv = ("import", "-silent", "-window", "root", *crop, "png:-")
        png = await self._import(argv, "capture")
        try:
            return Frame.from_png(png, self._clock.now())
        except ValueError as error:
            # import exited 0 but wrote no image: a display that vanished mid-capture.
            raise PeripheralError(_PERIPHERAL, "capture", str(error)) from error

    async def region_digest(self, region: Region) -> str:
        """Return a sha256 of one region's raw RGB pixels; see CompoundEye."""
        argv = (
            "import",
            "-silent",
            "-window",
            "root",
            *self._crop_arguments(region),
            "-depth",
            "8",
            "rgb:-",
        )
        pixels = await self._import(argv, "digest")
        expected = region.width * region.height * _RGB_BYTES_PER_PIXEL
        if len(pixels) != expected:
            raise PeripheralError(
                _PERIPHERAL, "digest", f"captured {len(pixels)} bytes, expected {expected}"
            )
        return hashlib.sha256(pixels).hexdigest()

    def _crop_arguments(self, region: Region) -> tuple[str, ...]:
        """Return import's crop arguments for `region`, refusing one that leaves the screen."""
        if not region.fits(self._display.size):
            raise PeripheralError(
                _PERIPHERAL,
                "crop",
                f"region {region.spec()} does not fit the "
                f"{self._display.size.width}x{self._display.size.height} screen",
            )
        # +repage drops the crop's virtual canvas offset, so the output is exactly the region.
        return ("-crop", region.crop_geometry(), "+repage")

    async def _import(self, argv: tuple[str, ...], operation: str) -> bytes:
        """Run one import command against this display and return its stdout."""
        command = PeripheralCommand(
            argv=argv, timeout_s=CAPTURE_TIMEOUT_S, env=self._display.environment()
        )
        return await run_peripheral(self._session, command, _PERIPHERAL, operation)
