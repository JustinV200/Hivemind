"""Integration: the Exoskeleton's X11 backends drive a display on a device through its session.

Roadmap step 11.11. Phase 6 built the Exoskeleton (`attach`, the CompoundEye over ImageMagick's
`import`, the Antennae over `xdotool`) so that every peripheral reaches its Cell only through that
Cell's `CellSession`. A borrowed device is a Cell whose session is a `PollenSession`, so the same
backends should work on it unchanged, with nothing on the Hive Stand but the session. This proves
it across a real boundary: a Pollen Packet in a container whose image carries an X server and its
tools (the device image's `EXTRA_PACKAGES` variant) is leased through `SwarmSource`, a desktop is
attached to the lease, and the backends are used through their own APIs: the display is seen (a
frame, then a region that changes when a window opens), a click lands on the window where it was
aimed, and a key reaches it. The window is `xev`, which prints every event it receives, so the
click and the key are read back from what the device's own X server delivered, not inferred.

Nothing here reaches the host: while the test runs, every program this process starts is recorded,
and none of the X11 programs is among them (a host that has Xvfb installed, as the CI integration
job's does, could otherwise hide a backend that ran something locally). Afterwards the lease's
scratch is gone, no Exoskeleton process is left in the container and its lease files are as they
were. `@pytest.mark.integration` (codingrules 14.2): skips cleanly when no Docker daemon answers or
`HIVEMIND_TEST_DEVICE_IMAGE` names an image the daemon lacks. The first run builds the image
variant (a few minutes: the X server and ImageMagick are a few hundred megabytes of packages).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped. Exercises
    `hivemind.exoskeleton.attach`, `compound_eye.x11` and `antennae.xdotool` through
    `hivemind.swarm.session.PollenSession` against pollen in a container, through `e2e.swarm`.

Key invariants:
    - The container is always removed, whatever the test did: the janitor closes in a `finally`.
    - Waits are bounded polls of something the device really did, never fixed sleeps.

See Also:
    - tests/integration/test_pollen_in_container.py for the session this builds on.
    - tests/integration/test_exoskeleton_docker.py for the same peripherals in a Virtual Cell.
    - e2e/swarm/device/Dockerfile for the `EXTRA_PACKAGES` variant.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
import pytest_asyncio
from builders.cells import make_identity
from builders.swarm.leasing import LeasedDevice, lease_device
from builders.swarm.live import LiveHive
from e2e.swarm import (
    DATA_DIR,
    ContainerDevice,
    ImageUnavailableError,
    Janitor,
    daemon_answers,
    device_image,
    enrol_container,
    start_stand,
)
from e2e.swarm.container import Container, FileEntry

from hivemind.cell.session import BackgroundSpec
from hivemind.exoskeleton import (
    Antennae,
    AttachDeps,
    CompoundEye,
    ExoskeletonConfig,
    ExoskeletonHandle,
    Point,
    Region,
    attach,
)
from hivemind.guard import CapabilitySet
from waggle.messages.capping import MouseButton
from waggle.messages.task import ExoskeletonNeed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

# What `subprocess.Popen` is started with: one program, or the program and its arguments.
type Program = str | bytes | os.PathLike[str] | os.PathLike[bytes]

# What a desktop needs on the device: the packet's probe wants all four programs it names
# (Xvfb, xdotool, import, openbox), and `xev` of x11-utils is the window that reports its events.
X11_PACKAGES = ("xvfb", "xdotool", "x11-utils", "imagemagick", "openbox")
# The programs the Exoskeleton runs on a device; none of them may ever be started on this host.
X11_PROGRAMS = frozenset({"Xvfb", "openbox", "xdotool", "import", "xev"})
TEST_TIMEOUT_S = 240.0  # A whole scenario, a cold packet and a display start included.
READY_TIMEOUT_S = 90.0  # Attach's own budget for Xvfb and openbox on a cold container.
WAIT_S = 20.0  # How long the device may take to show a window or deliver an event.
SETTLE_S = 10.0  # How long a stopped program may take to leave the container's process table.
POLL_S = 0.25
DEVICE_TMP = "/tmp"  # noqa: S108  # A directory inside the device, never one on this host.
SCREEN = (1280, 800)  # attach's default display size.
WINDOW = "600x400+100+100"  # xev's window: size and position, on the 1280x800 screen.
INSIDE_WINDOW = Point(400, 300)  # Well inside the window, clear of any frame openbox draws.
WINDOW_PATCH = Region(x=380, y=280, width=40, height=40)  # A patch of that window's inside.


@dataclass(frozen=True, slots=True)
class Rig:
    """The Hive Stand and the container device, with an X server installed, enrolled on it."""

    hive: LiveHive
    device: ContainerDevice


@pytest.fixture(scope="module")
def image() -> str:
    """The device image with an X server, built on first use; the module skips without a daemon."""
    if not daemon_answers():
        pytest.skip("no Docker daemon reachable")
    try:
        return device_image(X11_PACKAGES)
    except ImageUnavailableError as missing:
        pytest.skip(str(missing))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def rig(image: str, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Rig]:
    """A Hive Stand on the docker bridge and a packet in a container enrolled on it."""
    janitor, hive = Janitor(), None
    try:
        hive = await start_stand(tmp_path_factory.mktemp("stand"))
        yield Rig(hive, await enrol_container(hive, janitor, image))
    finally:
        janitor.close()  # The container first, so the Stand sees a device go, not a Stand go.
        if hive is not None:
            await hive.stop()


@pytest.fixture
def host_spawns(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the base name of every program this process starts while a test runs.

    `subprocess.Popen` is what `subprocess.run` and asyncio's subprocesses both start programs
    with, so one wrapper sees them all; the test then checks the recorder saw the docker command
    line (it is how the device is looked into) and none of the X11 programs.
    """
    spawned: list[str] = []
    # Typed loosely on purpose: this wraps the standard library's own constructor, whose many
    # overloads are passed through untouched.
    original = cast("Callable[..., None]", subprocess.Popen.__init__)

    def recording(
        self: object, args: Program | Sequence[Program], *rest: object, **options: object
    ) -> None:
        first = args if isinstance(args, str | bytes | os.PathLike) else args[0]
        spawned.append(os.path.basename(os.fsdecode(first)))
        original(self, args, *rest, **options)

    monkeypatch.setattr(subprocess.Popen, "__init__", recording)
    return spawned


async def _until(what: str, found: Callable[[], Awaitable[bool]]) -> None:
    """Poll `found` until it is true, or fail naming `what` after `WAIT_S`."""
    deadline = time.monotonic() + WAIT_S
    while not await found():
        if time.monotonic() > deadline:
            pytest.fail(f"The device did not show {what} within {WAIT_S:g} s.")
        await asyncio.sleep(POLL_S)


async def _settled_to(container: Container, expected: dict[int, str]) -> dict[int, str]:
    """Poll the container's process table until it equals `expected`, or `SETTLE_S` has passed."""
    deadline = time.monotonic() + SETTLE_S
    while True:
        table = await asyncio.to_thread(container.processes)
        if table == expected or time.monotonic() > deadline:
            return table
        await asyncio.sleep(POLL_S)


def _files_under(container: Container, path: str) -> dict[str, FileEntry]:
    """Snapshot a directory, or nothing if it is not there (the packet makes `leases` lazily)."""
    return container.file_tree(path) if container.exists(path) else {}


def _tmp_without_the_x_directory(container: Container) -> dict[str, FileEntry]:
    """Snapshot /tmp less `.X11-unix`, the one directory an X server leaves behind.

    A machine that has run an X server has `/tmp/.X11-unix` from boot (a desktop does). On one
    that never has, the first Xvfb makes it, removes its socket and lock file when it stops, and
    leaves the empty directory: shared system space no lease could tidy. Anything else under /tmp
    or in that directory is a leak, and shows in this snapshot.
    """
    return {path: e for path, e in container.file_tree(DEVICE_TMP).items() if path != ".X11-unix"}


async def _attach_desktop(hive: LiveHive, leased: LeasedDevice) -> ExoskeletonHandle:
    """Attach a desktop to the lease, as the Warden's spawn path would, and check it is up."""
    cell = (await leased.source.cells())[0]
    # The packet probed the programs at enrolment: the variant image is what makes this true.
    assert cell.capabilities.can_start_display
    deps = AttachDeps(
        trail=hive.trail,
        identity=make_identity(hive.clock, hive_id=hive.party.hive_id, node_id=hive.party.node_id),
        clock=hive.clock,
        config=ExoskeletonConfig(ready_timeout_s=READY_TIMEOUT_S),
    )
    granted = CapabilitySet.parse("exoskeleton:display")
    return await attach(cell, leased.session, ExoskeletonNeed(), granted, deps)


def _peripherals(handle: ExoskeletonHandle) -> tuple[CompoundEye, Antennae]:
    """The eye and the antennae a desktop attach provides, both present or the test fails."""
    eye, antennae = handle.peripherals.compound_eye, handle.peripherals.antennae
    assert eye is not None
    assert antennae is not None
    return eye, antennae


async def _open_window(leased: LeasedDevice, handle: ExoskeletonHandle) -> Path:
    """Start `xev` on the device's display and return the log it writes its events to.

    `stdbuf` makes `xev` write each event as it happens: its output goes to a file, which the C
    library would otherwise buffer until a page is full.
    """
    session = leased.session
    log = session.scratch_dir / "xev.log"
    argv = ("stdbuf", "-oL", "xev", "-geometry", WINDOW)
    await session.start(BackgroundSpec(argv=argv, env=dict(handle.environment), log_path=log))

    async def mapped() -> bool:
        return b"MapNotify" in await _read(leased, log)

    await _until("the xev window", mapped)
    return log


async def _read(leased: LeasedDevice, log: Path) -> bytes:
    """Read a log on the device, or nothing before the program has written its first line."""
    try:
        return await leased.session.get_file(log)
    except FileNotFoundError:
        return b""


async def _see(leased: LeasedDevice, handle: ExoskeletonHandle) -> Path:
    """Use the eye: a frame of the whole display, and a region that changes when a window opens."""
    eye, _ = _peripherals(handle)
    frame = await eye.capture()
    assert (frame.width, frame.height) == SCREEN
    assert frame.png.startswith(b"\x89PNG")
    empty = await eye.region_digest(WINDOW_PATCH)
    log = await _open_window(leased, handle)
    assert await eye.region_digest(WINDOW_PATCH) != empty  # The patch is now inside the window.
    assert (await eye.capture()).sha256 != frame.sha256
    return log


async def _touch(leased: LeasedDevice, handle: ExoskeletonHandle, log: Path) -> None:
    """Use the antennae: a click aimed at a point, then a key; read both from the window's log."""
    _, antennae = _peripherals(handle)
    await antennae.click(INSIDE_WINDOW, MouseButton.LEFT)

    async def clicked() -> bool:
        return b"ButtonPress" in await _read(leased, log)

    await _until("the click", clicked)
    seen = (await _read(leased, log)).decode(errors="replace")
    # The server reports the pointer in screen coordinates: exactly where the click was aimed.
    assert f"root:({INSIDE_WINDOW.x},{INSIDE_WINDOW.y})" in seen
    assert "button 1" in seen

    await antennae.press("a")

    async def typed() -> bool:
        return b"keysym 0x61, a" in await _read(leased, log)

    await _until("the key", typed)
    assert await antennae.pointer() == INSIDE_WINDOW  # And the pointer is where the click left it.


async def test_the_packet_reports_the_x11_programs_the_variant_image_installs(rig: Rig) -> None:
    node = await rig.hive.node(rig.device.node.device_id)

    assert node.capabilities.can_start_display  # Xvfb, xdotool, import and openbox are on PATH.
    assert not node.capabilities.has_display  # But nothing runs at boot: attach starts it.


async def test_the_x11_backends_drive_a_display_on_the_device_through_the_session_alone(
    rig: Rig, host_spawns: list[str]
) -> None:
    container = rig.device.container
    processes_before = container.processes()
    leases_before = _files_under(container, f"{DATA_DIR}/leases")
    tmp_before = _tmp_without_the_x_directory(container)

    async with asyncio.timeout(TEST_TIMEOUT_S):
        leased = await lease_device(rig.hive, rig.device.node, PurePosixPath(DATA_DIR))
        scratch = leased.session.scratch_dir
        handle = await _attach_desktop(rig.hive, leased)
        # The display is the device's own: its X server and window manager are in its process table.
        assert {"Xvfb", "openbox"} <= set(container.processes().values())

        log = await _see(leased, handle)
        await _touch(leased, handle, log)

        detached = await handle.detach()
        await leased.session.close()
        released = await leased.lease.release()

    assert detached.still_running == 0
    assert detached.stopped >= 2  # Xvfb and openbox.
    assert released.is_restored
    assert not container.exists(str(scratch))
    assert _files_under(container, f"{DATA_DIR}/leases") == leases_before
    assert _tmp_without_the_x_directory(container) == tmp_before
    assert await _settled_to(container, processes_before) == processes_before
    # The recorder saw the docker command line, so it saw this process start programs at all.
    assert "docker" in host_spawns
    assert not X11_PROGRAMS & set(host_spawns)
