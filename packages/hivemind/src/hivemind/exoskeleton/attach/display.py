"""Start a lease's own X display, or borrow the one its operator allowed, and prove it takes input.

A desktop Exoskeleton needs an X display the CompoundEye can capture and the Antennae can drive.
For a lease display, attach writes a fresh MIT cookie into the lease's scratch, starts Xvfb with
`-displayfd 1` (Xvfb picks a free display number itself and prints it once it accepts clients),
`-nolisten tcp` and `-auth` on that cookie file, reads the number back from Xvfb's log, then starts
openbox against it with HOME inside scratch and the lease's own configuration (`attach.openbox`),
whose desktop and keys launch nothing. The window manager is load-bearing: without one,
`xdotool mousemove` succeeds and the pointer never moves (ADR-0031). So readiness is the one check
that matters to a bee: the pointer is moved to a probe point and read back until it is there.
Borrowing the operator's running display (`DisplaySource.RUNNING`) starts nothing: it reads the
display the session inherits, refuses anything but a local ":N" X display, and asks it its size.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.attach`.
    Called by `attach.core`. Calls into `hivemind.cell` (CellSession, BackgroundSpec, ExecSpec,
    run), `hivemind.common.logging`, `hivemind.exoskeleton.antennae.xdotool`, `.commands`,
    `.errors`, `.geometry`, `.scratch`, `.x11`, `attach.openbox` and `attach.ready` only.

Key invariants:
    - Every process started here is stopped again before an error leaves this module.
    - The display listens on no TCP port and admits only holders of this lease's cookie.
    - Openbox never reads the system configuration: its menus would launch any installed program.
    - X11_PROGRAMS and WINDOW_MANAGER match what `hivemind.cell.local.probe` requires before it
      reports `can_start_display` (a test holds them together).

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for private displays.
    - hivemind.exoskeleton.x11 for the cookie file's format.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from hivemind.cell import BackgroundProcess, BackgroundSpec, CellSession, ExecSpec, run
from hivemind.common.logging import get_logger
from hivemind.exoskeleton.antennae.xdotool import XdotoolAntennae
from hivemind.exoskeleton.attach.openbox import write_openbox_config
from hivemind.exoskeleton.attach.ready import Deadline, start_process, stop_all
from hivemind.exoskeleton.commands import sanitised
from hivemind.exoskeleton.errors import AttachError, PeripheralError
from hivemind.exoskeleton.geometry import Point, ScreenSize
from hivemind.exoskeleton.scratch import ScratchLayout
from hivemind.exoskeleton.x11 import COOKIE_BYTES, X11Display, authority_file

X11_PROGRAMS = ("Xvfb", "xdotool", "import")  # What a lease display is started and used with.
WINDOW_MANAGER = "openbox"  # The one window manager proven to make xdotool's moves take effect.
XVFB_DEPTH = 24  # Bits per pixel: true colour, what every application expects.
QUERY_TIMEOUT_S = 10.0  # One short query of a running display.
_PROBE_POINT = Point(7, 5)  # Where readiness moves the pointer; on every screen, off its origin.
_DISPLAY_NUMBER = re.compile(rb"^(\d{1,4})$", re.MULTILINE)  # -displayfd prints just the number.
_GEOMETRY = re.compile(rb"^(\d{1,5}) (\d{1,5})$")  # `xdotool getdisplaygeometry`: "W H".

log = get_logger(__name__)

__all__ = [
    "QUERY_TIMEOUT_S",
    "WINDOW_MANAGER",
    "X11_PROGRAMS",
    "XVFB_DEPTH",
    "StartedDisplay",
    "borrow_running_display",
    "start_lease_display",
]


@dataclass(frozen=True, slots=True)
class StartedDisplay:
    """A display attach can use, and the processes it started for it (none when borrowed)."""

    display: X11Display
    processes: tuple[BackgroundProcess, ...]


async def start_lease_display(
    session: CellSession, layout: ScratchLayout, screen: ScreenSize, deadline: Deadline
) -> StartedDisplay:
    """Start Xvfb and openbox for this lease and return once the pointer takes input.

    Args:
        session: The Cell's session; both processes start through it.
        layout: Where the cookie, logs and HOME go.
        screen: The display's size.
        deadline: The whole start-up's budget.

    Returns:
        The ready display and its two processes, Xvfb first.

    Raises:
        AttachError: A process could not start, exited early, or the display never took input
            within the deadline. Anything started is already stopped.
    """
    await session.put_file(layout.authority, authority_file(secrets.token_bytes(COOKIE_BYTES)))
    started: list[BackgroundProcess] = []
    try:
        xvfb = await start_process(session, _xvfb_spec(layout, screen))
        started.append(xvfb)
        number = await _await_display_number(session, xvfb, layout.x11_dir / "xvfb.log", deadline)
        display = X11Display(name=f":{number}", authority=layout.authority, size=screen)
        env = {**layout.home_environment(), **display.environment()}
        log = layout.x11_dir / "openbox.log"
        # WHY: the lease's own configuration, never the system one, whose menus launch programs.
        rc = await write_openbox_config(session, layout)
        argv = (WINDOW_MANAGER, "--sm-disable", "--config-file", rc.as_posix())
        wm = await start_process(session, BackgroundSpec(argv=argv, env=env, log_path=log))
        started.append(wm)
        await _await_pointer(session, display, deadline)
    except BaseException:
        # Leave nothing behind whatever went wrong, cancellation included, then let it propagate.
        await stop_all(session, started)
        raise
    return StartedDisplay(display=display, processes=tuple(started))


async def borrow_running_display(session: CellSession) -> StartedDisplay:
    """Describe the display already running on the Cell, which its operator allowed the Hive to use.

    Args:
        session: The Cell's session, whose inherited environment names the display.

    Returns:
        The display, with no authority file of its own (the session's own XAUTHORITY applies)
        and no processes.

    Raises:
        AttachError: No local X display is named, or it does not answer a size query.
    """
    named = await run(session, ExecSpec(argv=("printenv", "DISPLAY"), timeout_s=QUERY_TIMEOUT_S))
    name = named.stdout.decode("utf-8", errors="replace").strip()
    try:
        probe = X11Display(name=name, authority=None, size=ScreenSize(1, 1))
    except ValueError as error:
        raise AttachError("the Cell's own display is not a local X display (:N)") from error
    geometry = await run(
        session,
        ExecSpec(
            argv=("xdotool", "getdisplaygeometry"),
            env=probe.environment(),
            timeout_s=QUERY_TIMEOUT_S,
        ),
    )
    match = _GEOMETRY.match(geometry.stdout.strip())
    if geometry.exit_code != 0 or match is None:
        raise AttachError(f"the Cell's own display {name} did not report its size")
    size = ScreenSize(int(match.group(1)), int(match.group(2)))
    return StartedDisplay(display=X11Display(name=name, authority=None, size=size), processes=())


def _xvfb_spec(layout: ScratchLayout, screen: ScreenSize) -> BackgroundSpec:
    """Build Xvfb's command: its own display number, no TCP, this lease's cookie only."""
    geometry = f"{screen.width}x{screen.height}x{XVFB_DEPTH}"
    argv: tuple[str, ...] = ("Xvfb", "-displayfd", "1", "-screen", "0", geometry)
    # -noreset keeps the server (and its cookie) up between the last client leaving and the next.
    argv += ("-nolisten", "tcp", "-auth", layout.authority.as_posix(), "-noreset")
    log = layout.x11_dir / "xvfb.log"
    return BackgroundSpec(argv=argv, env=layout.home_environment(), log_path=log)


async def _await_display_number(
    session: CellSession, xvfb: BackgroundProcess, log: Path, deadline: Deadline
) -> int:
    """Wait for Xvfb to print its display number, which it does once it accepts clients."""
    while True:
        text = await _read_log(session, log)
        match = _DISPLAY_NUMBER.search(text)
        if match is not None:
            return int(match.group(1))
        if not await session.is_running(xvfb):
            raise AttachError(f"Xvfb exited before it was ready: {sanitised(text)}")
        if deadline.expired():
            raise AttachError(f"Xvfb did not report a display within {deadline.seconds}s")
        await deadline.pause()


async def _await_pointer(session: CellSession, display: X11Display, deadline: Deadline) -> None:
    """Wait until moving the pointer really moves it: the window manager is up and managing."""
    antennae = XdotoolAntennae(session, display)
    while True:
        try:
            await antennae.move(_PROBE_POINT)
            if await antennae.pointer() == _PROBE_POINT:
                return
        except PeripheralError as error:
            # The display or window manager is still coming up; the deadline bounds the retries.
            log.debug("exoskeleton.display_not_ready", reason=error.reason)
        if deadline.expired():
            raise AttachError(f"the display did not take pointer input within {deadline.seconds}s")
        await deadline.pause()


async def _read_log(session: CellSession, log: Path) -> bytes:
    """Return a started process's log so far, or nothing before the process has written it."""
    try:
        return await session.get_file(log)
    except FileNotFoundError:
        return b""  # The log file appears with the process's first write.
