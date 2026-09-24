"""Provide one desktop per Exoskeleton implementation, for the peripheral contract suites.

The Exoskeleton contract suite (roadmap step 6.8) states each CompoundEye, Antennae and Buzz
clause once and runs it over every implementation: the fakes (`FakeDesktopHarness`: a
`FakeScreen`, a `FakeAntennae`, a `FakeBuzz` over a `FakeSession`) and the real X11 and
PulseAudio backends (`RealDesktopHarness`: `hivemind.exoskeleton.attach` on a real
`LocalProcessSession`, so a real Xvfb, openbox and PulseAudio start for the test and stop after
it). A clause that needs the world to change does it through the `Desktop` it is handed: `show`
puts a picture on the screen (white, with one rectangle in one colour; the fake repaints its
FakeScreen, the real one sets the X root window with ImageMagick's `display`), and `play` puts a
clip on the speaker (the fake scripts its next recording; the real one starts `paplay`). The real
harness runs wherever the tools are installed and skips, naming what is missing, where they are
not.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by
    `contracts.test_exoskeleton_contract`.

Key invariants:
    - The real harness works in a short scratch directory under /tmp, because the sound server's
      socket path must fit a Unix socket address and pytest's own tmp_path may not.
    - Every real process the harness starts is stopped in `close`, and its scratch is removed.

See Also:
    - hivemind.exoskeleton.attach for how the real desktop is started.
"""

from __future__ import annotations

import shutil
import struct
import tempfile
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from builders.cells import (
    make_capabilities,
    make_cell,
    make_hive_stand_releaser,
    make_identity,
    make_real_cell_lease,
)

from hivemind.cell import BackgroundSpec, CellSession, ExecSpec, run
from hivemind.cell.fake import FakeSession
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.session import LocalProcessSession
from hivemind.exoskeleton.antennae import Antennae, FakeAntennae
from hivemind.exoskeleton.attach import (
    AUDIO_PROGRAMS,
    WINDOW_MANAGER,
    X11_PROGRAMS,
    AttachDeps,
    ExoskeletonConfig,
    ExoskeletonHandle,
    attach,
)
from hivemind.exoskeleton.buzz import Buzz, FakeBuzz, Recording
from hivemind.exoskeleton.compound_eye import WHITE, CompoundEye, FakeCompoundEye, FakeScreen, Rgb
from hivemind.exoskeleton.frames import PNG_SIGNATURE
from hivemind.exoskeleton.geometry import Region, ScreenSize
from hivemind.guard import CapabilitySet
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock, SystemClock
from waggle.messages.task import ExoskeletonNeed

SCREEN = ScreenSize(320, 240)  # Small enough to capture quickly; big enough for every clause.
_PAINTER = "display"  # ImageMagick's viewer: `-window root` makes a picture the root window.
_REAL_PROGRAMS = (*X11_PROGRAMS, WINDOW_MANAGER, *AUDIO_PROGRAMS, _PAINTER)
_QUOTA_BYTES = 1 << 30  # Frames and clips are small; the quota is not what these tests exercise.
_PAINT_TIMEOUT_S = 10.0

__all__ = ["HARNESSES", "SCREEN", "Desktop", "DesktopHarness"]

Show = Callable[[Region, Rgb], Awaitable[None]]
Play = Callable[[Recording], Awaitable[None]]


@dataclass
class Desktop:
    """One desktop under test: its peripherals, its session, and how to change the world."""

    session: CellSession
    eye: CompoundEye
    antennae: Antennae
    buzz: Buzz
    show: Show  # Put a white screen with `region` in one colour on the display.
    play: Play  # Put a recording on the speaker, where `listen` hears it.


class DesktopHarness(Protocol):
    """Opens a desktop for one test and closes it after."""

    name: str

    def missing(self) -> str | None:
        """Return why this harness cannot run here, or None when it can."""
        ...

    async def open(self) -> Desktop:
        """Start a fresh desktop."""
        ...

    async def close(self) -> None:
        """Stop everything `open` started."""
        ...


class FakeDesktopHarness:
    """The fakes, over a FakeSession: always available."""

    name = "fake"

    def missing(self) -> str | None:
        return None

    async def open(self) -> Desktop:
        self._scratch = Path(tempfile.mkdtemp(prefix="hm-fake-"))
        session = FakeSession(self._scratch, FakeClock())
        screen = FakeScreen(SCREEN)
        buzz = FakeBuzz(session)

        async def show(region: Region, colour: Rgb) -> None:
            screen.clear()
            screen.paint(region, colour)

        async def play(recording: Recording) -> None:
            buzz.script(recording)  # The fake hears exactly what was played.

        eye = FakeCompoundEye(screen, FakeClock())
        return Desktop(session, eye, FakeAntennae(SCREEN), buzz, show, play)

    async def close(self) -> None:
        shutil.rmtree(self._scratch, ignore_errors=True)


class RealDesktopHarness:
    """The X11 and PulseAudio backends, attached on a real LocalProcessSession."""

    name = "real"

    def missing(self) -> str | None:
        absent = [program for program in _REAL_PROGRAMS if shutil.which(program) is None]
        return f"not installed here: {', '.join(absent)}" if absent else None

    async def open(self) -> Desktop:
        clock = SystemClock()
        # Short on purpose: the sound server's socket must fit a Unix socket address.
        self._scratch = Path(tempfile.mkdtemp(prefix="hm-", dir="/tmp"))
        lease = make_real_cell_lease(
            self._scratch, clock=clock, releaser=make_hive_stand_releaser(clock=clock)
        )
        self._session = LocalProcessSession(lease, ScratchQuota(quota_bytes=_QUOTA_BYTES), clock)
        cell = make_cell(capabilities=make_capabilities(can_start_display=True, has_audio=True))
        granted = CapabilitySet.parse("exoskeleton:display", "exoskeleton:audio")
        trail, identity = MemoryPheromoneTrail(clock), make_identity(clock=clock)
        deps = AttachDeps(trail, identity, clock, config=ExoskeletonConfig(screen=SCREEN))
        need = ExoskeletonNeed(audio=True)
        self._handle = await attach(cell, self._session, need, granted, deps)
        return self._desktop(self._handle)

    async def close(self) -> None:
        await self._handle.detach()
        await self._session.close()
        shutil.rmtree(self._scratch, ignore_errors=True)

    def _desktop(self, handle: ExoskeletonHandle) -> Desktop:
        session, env = self._session, dict(handle.environment)
        eye, antennae = handle.peripherals.compound_eye, handle.peripherals.antennae
        buzz = handle.peripherals.buzz
        assert eye is not None and antennae is not None and buzz is not None

        async def show(region: Region, colour: Rgb) -> None:
            await session.put_file(Path("shown.png"), picture_png(SCREEN, region, colour))
            argv = (_PAINTER, "-window", "root", "shown.png")
            # `display -window root` exits 1 even when it painted; the clauses check the pixels.
            await run(session, ExecSpec(argv=argv, env=env, timeout_s=_PAINT_TIMEOUT_S))

        async def play(recording: Recording) -> None:
            await session.put_file(Path("played.wav"), recording.wav)
            # Plays while the clause listens; the lease's session stops it if it outlives it.
            await session.start(BackgroundSpec(argv=("paplay", "played.wav"), env=env))

        return Desktop(session, eye, antennae, buzz, show, play)


def picture_png(size: ScreenSize, region: Region, colour: Rgb) -> bytes:
    """Encode a white RGB picture of `size` with `region` filled in `colour`."""
    rows = []
    for y in range(size.height):
        inside = region.y <= y < region.y + region.height
        left, right = (region.x, region.x + region.width) if inside else (0, 0)
        pixels = bytes(WHITE) * left + bytes(colour) * (right - left)
        rows.append(b"\x00" + pixels + bytes(WHITE) * (size.width - right))
    header = struct.pack(">IIBBBBB", size.width, size.height, 8, 2, 0, 0, 0)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Frame one PNG chunk with its length and CRC."""
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


# Factories, not instances: each test gets a fresh harness, so no state crosses tests.
HARNESSES: tuple[Callable[[], DesktopHarness], ...] = (FakeDesktopHarness, RealDesktopHarness)
