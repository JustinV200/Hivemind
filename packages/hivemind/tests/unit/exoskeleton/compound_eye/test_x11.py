"""Unit tests for hivemind.exoskeleton.compound_eye.x11: the commands X11CompoundEye runs."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from hivemind.cell import CompletedCommand, ExecSpec
from hivemind.cell.fake import FakeSession
from hivemind.exoskeleton.compound_eye.x11 import X11CompoundEye
from hivemind.exoskeleton.errors import PeripheralError
from hivemind.exoskeleton.frames import solid_png
from hivemind.exoskeleton.geometry import Region, ScreenSize
from hivemind.exoskeleton.x11 import X11Display
from waggle.clock import FakeClock

_DISPLAY = X11Display(name=":4", authority=Path("/s/.Xauthority"), size=ScreenSize(200, 100))


def _eye(tmp_path: Path, stdout: bytes, seen: list[ExecSpec], code: int = 0) -> X11CompoundEye:
    def respond(spec: ExecSpec) -> CompletedCommand:
        seen.append(spec)
        return CompletedCommand(exit_code=code, stdout=stdout, stderr=b"no display", duration_s=0)

    return X11CompoundEye(
        FakeSession(tmp_path, FakeClock(), responder=respond), _DISPLAY, FakeClock()
    )


async def test_capture_runs_import_on_the_root_window_of_this_display(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []

    frame = await _eye(tmp_path, solid_png(200, 100, (0, 0, 0)), seen).capture()

    assert (frame.width, frame.height) == (200, 100)
    assert seen[0].argv == ("import", "-silent", "-window", "root", "png:-")
    assert seen[0].env == {"DISPLAY": ":4", "XAUTHORITY": "/s/.Xauthority"}


async def test_a_region_capture_crops_and_repages(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []
    region = Region(x=5, y=6, width=20, height=10)

    await _eye(tmp_path, solid_png(20, 10, (0, 0, 0)), seen).capture(region)

    assert seen[0].argv[4:7] == ("-crop", "20x10+5+6", "+repage")


async def test_a_region_off_the_screen_is_refused_before_any_command(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []

    with pytest.raises(PeripheralError, match="does not fit"):
        await _eye(tmp_path, b"", seen).capture(Region(x=190, y=0, width=20, height=10))

    assert seen == []


async def test_an_empty_capture_is_an_error_not_a_frame(tmp_path: Path) -> None:
    with pytest.raises(PeripheralError, match="Not a PNG"):
        await _eye(tmp_path, b"", []).capture()


async def test_a_failed_import_is_a_peripheral_error_naming_the_tool(tmp_path: Path) -> None:
    with pytest.raises(PeripheralError, match="import exited 1: no display"):
        await _eye(tmp_path, b"", [], code=1).capture()


async def test_region_digest_hashes_exactly_the_regions_raw_rgb(tmp_path: Path) -> None:
    seen: list[ExecSpec] = []
    pixels = bytes(range(6)) * 2  # 2x2 pixels, 3 bytes each.

    digest = await _eye(tmp_path, pixels, seen).region_digest(Region(x=0, y=0, width=2, height=2))

    assert digest == hashlib.sha256(pixels).hexdigest()
    assert seen[0].argv[-3:] == ("-depth", "8", "rgb:-")


async def test_a_truncated_region_capture_is_refused_rather_than_digested(tmp_path: Path) -> None:
    with pytest.raises(PeripheralError, match="captured 5 bytes, expected 12"):
        await _eye(tmp_path, b"12345", []).region_digest(Region(x=0, y=0, width=2, height=2))
