"""Build what Exoskeleton tests attach to: scripted desktop sessions, a stub browser, a clock pump.

`attach` runs real commands through a CellSession: `mkdir`, Xvfb and openbox starts, `xdotool`
probes, `pactl info`, `printenv DISPLAY`. `ScriptedDesktop` answers those the way a working desktop
Cell would (or a broken one, per its flags), so attach's whole flow, failure paths included, runs
against a `FakeSession` with no X server. `desktop_session` wires one up with Xvfb scripted to print
its display number; `drive` runs a coroutine that waits on a `FakeClock`, advancing it one poll
interval at a time; `StubLauncher` starts a fake browser process through the session and hands back
a minimal Browser that records whether it was closed.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the unit tests under
    packages/hivemind/tests/unit/exoskeleton/attach.

Key invariants:
    - Every scripted answer mirrors what the real tool prints (xdotool's `--shell` form,
      `getdisplaygeometry`'s "W H", Xvfb's -displayfd number on a line of its own).

See Also:
    - hivemind.exoskeleton.attach for the code under test.
    - hivemind.cell.fake.FakeSession for exec and start scripting.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, cast

from hivemind.cell import BackgroundSpec, CellSession, CompletedCommand, ExecSpec
from hivemind.cell.fake import FakeSession, FakeStart
from hivemind.exoskeleton.attach.ready import POLL_INTERVAL_S
from hivemind.exoskeleton.browser import Browser, BrowserLaunch, LaunchedBrowser
from waggle.clock import Clock, FakeClock

DISPLAY_NUMBER = 7  # What the scripted Xvfb reports through -displayfd.
XVFB_LOG = b"(WW) a warning Xvfb prints first\n7\n"  # Warnings, then the number on its own line.
_PUMP_LIMIT = 2_000  # Clock advances before `drive` gives up: far past any test's deadline.

__all__ = [
    "DISPLAY_NUMBER",
    "XVFB_LOG",
    "ScriptedDesktop",
    "StubLauncher",
    "desktop_session",
    "drive",
]


class ScriptedDesktop:
    """Answer attach's commands as a desktop Cell would; flags make it misbehave."""

    def __init__(
        self,
        *,
        pointer_moves: bool = True,
        sound_ready_after: int = 0,
        running_display: str | None = None,
    ) -> None:
        """Build the responder.

        Args:
            pointer_moves: False makes the pointer ignore moves, as a bare Xvfb does.
            sound_ready_after: How many `pactl info` probes fail before the server answers.
            running_display: What `printenv DISPLAY` prints; None means unset.
        """
        self.pointer_moves = pointer_moves
        self.sound_ready_after = sound_ready_after
        self.running_display = running_display
        self.calls: list[ExecSpec] = []
        self._pointer = (640, 400)

    def __call__(self, spec: ExecSpec) -> CompletedCommand:
        """Answer one exec the way the real program would."""
        self.calls.append(spec)
        program, args = spec.argv[0], spec.argv[1:]
        if program == "xdotool":
            return self._xdotool(args)
        if program == "pactl":
            self.sound_ready_after -= 1
            return _done(0 if self.sound_ready_after < 0 else 1)
        if program == "printenv":
            shown = self.running_display
            return _done(0, f"{shown}\n".encode()) if shown is not None else _done(1)
        if program == "mkdir":
            return _done(0)
        return _done(127, stderr=b"command not found")

    def programs(self) -> list[str]:
        """Return the program of every exec so far, in order."""
        return [spec.argv[0] for spec in self.calls]

    def _xdotool(self, args: tuple[str, ...]) -> CompletedCommand:
        """Answer an xdotool command: moves, the pointer read-back and the screen size."""
        if args[0] == "mousemove" and self.pointer_moves:
            self._pointer = (int(args[-2]), int(args[-1]))
        if args[0] == "getmouselocation":
            x, y = self._pointer
            return _done(0, f"X={x}\nY={y}\nSCREEN=0\nWINDOW=1\n".encode())
        if args[0] == "getdisplaygeometry":
            return _done(0, b"1920 1080\n")
        return _done(0)


def desktop_session(
    tmp_path: Path, clock: Clock, desktop: ScriptedDesktop | None = None
) -> FakeSession:
    """Build a FakeSession whose Xvfb reports DISPLAY_NUMBER and whose execs `desktop` answers."""
    session = FakeSession(tmp_path, clock, responder=desktop or ScriptedDesktop())
    session.script_start("Xvfb", FakeStart(log=XVFB_LOG))
    return session


async def drive[ResultT](clock: FakeClock, coro: Coroutine[Any, Any, ResultT]) -> ResultT:
    """Run `coro`, advancing `clock` one poll interval whenever it is waiting on it.

    Args:
        clock: The FakeClock `coro` sleeps on.
        coro: The coroutine to finish.

    Returns:
        Its result; its exception propagates.
    """
    task = asyncio.ensure_future(coro)
    for _ in range(_PUMP_LIMIT):
        await asyncio.sleep(0)  # Let it run up to its next clock wait.
        if task.done():
            break
        clock.advance(POLL_INTERVAL_S)
    return await task


class _StubBrowser:
    """The least Browser attach needs: something to close. Every other call is unused here."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class StubLauncher:
    """A BrowserLauncher that starts a fake "chromium" through the session and records requests."""

    def __init__(self) -> None:
        """Build a launcher that has launched nothing yet."""
        self.requests: list[BrowserLaunch] = []
        self.browser = _StubBrowser()

    async def launch(self, session: CellSession, request: BrowserLaunch) -> LaunchedBrowser:
        """Start one fake browser process and return the stub browser over it."""
        self.requests.append(request)
        process = await session.start(BackgroundSpec(argv=("chromium", "about:blank")))
        return LaunchedBrowser(browser=cast(Browser, self.browser), processes=(process,))


def _done(code: int, stdout: bytes = b"", stderr: bytes = b"") -> CompletedCommand:
    """A finished command with `code` and output."""
    return CompletedCommand(exit_code=code, stdout=stdout, stderr=stderr, duration_s=0.0)
