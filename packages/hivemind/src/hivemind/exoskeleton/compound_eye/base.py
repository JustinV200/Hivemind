"""Define CompoundEye: how a Worker sees its Cell's display, through the Cell's own session.

The CompoundEye (named for a bee's compound eyes) is the Exoskeleton's vision peripheral: it
captures what the Cell's display shows, as a whole-screen `Frame`, a cropped `Frame`, or a digest
of one region's raw pixels. The digest exists so the Capping gate can check a REGION_CHANGED
postcondition (did this rectangle change after the click?) by comparing two short strings, never by
decoding or storing images. Implementations run their capture commands through a `CellSession`
(codingrules section 8.7), so one backend serves a Virtual Cell and a Linux Real Cell alike.

Latency classes used below: *interactive* means tens to a few hundred milliseconds (one short
command on the Cell); every call is bounded by the implementation's own capture timeout.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton`.
    Implemented by `hivemind.exoskeleton.compound_eye.x11.X11CompoundEye` and
    `hivemind.exoskeleton.compound_eye.fake.FakeCompoundEye`; called by the `see` tool, the flight
    recorder and the Capping gate's GUI surface. Calls into `hivemind.exoskeleton.frames` and
    `.geometry` only.

Key invariants:
    - A capture never reaches a log or the trail; a Frame's own repr hides its bytes.
    - `region_digest` of an unchanged region returns the same string twice, and of a changed one
      a different string; it is a pure function of the region's pixels.

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the X11 backend choice.
    - hivemind.exoskeleton.frames for Frame.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.exoskeleton.frames import Frame
from hivemind.exoskeleton.geometry import Region, ScreenSize

__all__ = ["CompoundEye"]


class CompoundEye(Protocol):
    """See the Cell's display: capture it whole or in part, or fingerprint one region."""

    @property
    def screen(self) -> ScreenSize:
        """The display's size in pixels; fixed for the life of the attachment."""
        ...

    async def capture(self, region: Region | None = None) -> Frame:
        """Capture the display, or one rectangle of it, as a PNG Frame.

        Latency: interactive (one capture command; a few hundred milliseconds for a full HD
        screen). Failure: PeripheralError when the display is gone, the capture command exits
        non-zero or times out, or `region` does not fit the screen.

        Args:
            region: The rectangle to crop; None captures the whole screen.

        Returns:
            The captured Frame.

        Raises:
            PeripheralError: The capture failed (see the failure mode above).
        """
        ...

    async def region_digest(self, region: Region) -> str:
        """Return a digest of one region's raw pixels, to tell whether it changed.

        Latency: interactive (one capture command over the region only). Failure:
        PeripheralError, exactly as `capture`.

        Args:
            region: The rectangle to fingerprint; must fit the screen.

        Returns:
            A hex sha256 of the region's raw RGB pixels.

        Raises:
            PeripheralError: The capture failed or `region` does not fit the screen.
        """
        ...
