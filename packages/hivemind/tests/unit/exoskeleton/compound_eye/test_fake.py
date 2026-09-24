"""Unit tests for hivemind.exoskeleton.compound_eye.fake: FakeScreen and FakeCompoundEye."""

from __future__ import annotations

import pytest

from hivemind.exoskeleton.compound_eye.fake import FakeCompoundEye, FakeScreen
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.geometry import Point, Region, ScreenSize
from waggle.clock import FakeClock

_BUTTON = Region(x=10, y=10, width=30, height=10)


def _eye() -> tuple[FakeScreen, FakeCompoundEye]:
    screen = FakeScreen(ScreenSize(100, 60))
    return screen, FakeCompoundEye(screen, FakeClock())


async def test_a_digest_changes_when_the_region_is_repainted_a_new_colour() -> None:
    screen, eye = _eye()
    before = await eye.region_digest(_BUTTON)

    screen.paint(Region(x=15, y=12, width=5, height=2), (0, 128, 0))

    assert await eye.region_digest(_BUTTON) != before


async def test_a_digest_depends_only_on_the_pixels_not_on_how_they_were_painted() -> None:
    screen, eye = _eye()
    before = await eye.region_digest(_BUTTON)

    screen.paint(Region(x=60, y=40, width=10, height=10), (255, 0, 0))  # Outside the region.
    screen.paint(_BUTTON, (255, 255, 255))  # The colour it already is.
    screen.paint(Region(x=12, y=11, width=3, height=3), (255, 255, 255))

    assert await eye.region_digest(_BUTTON) == before


async def test_capture_returns_a_real_png_in_the_colour_at_the_areas_corner() -> None:
    screen, eye = _eye()
    screen.paint(_BUTTON, (1, 2, 3))

    frame = await eye.capture(_BUTTON)

    assert frame.png.startswith(b"\x89PNG")
    assert screen.colour_at(Point(_BUTTON.x, _BUTTON.y)) == (1, 2, 3)


async def test_a_region_off_the_screen_is_refused_as_the_real_backend_does() -> None:
    _, eye = _eye()

    with pytest.raises(PeripheralError, match="does not fit"):
        await eye.region_digest(Region(x=90, y=0, width=20, height=10))


async def test_a_vanished_display_fails_every_call() -> None:
    screen, eye = _eye()
    screen.vanish()

    with pytest.raises(PeripheralError, match="gone"):
        await eye.capture()
