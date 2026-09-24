"""Start a lease's Chromium through the Cell's session and connect to it: ChromiumLauncher.

The browser fast path's process belongs to the lease the way the display and the sound server do
(ADR-0031). `ChromiumLauncher` finds a Chromium-family browser on the Cell (`browser.locate`),
starts it through `CellSession.start` (so the lease records it and its release kills it as a
backstop) with its profile, log and temporary files in the lease's scratch, lets Chromium choose a
free DevTools port on loopback (`--remote-debugging-port=0`), waits for the port Chromium writes to
`DevToolsActivePort` in the profile, and hands `http://127.0.0.1:<port>` to a connector: by default
Playwright's `connect_over_cdp` (`browser.playwright`), imported only here and only at launch, so a
Hive without the `hivemind[browser]` extra imports this module fine and learns at attach time
what is missing. Two details keep a borrowed machine as it was found. Every lease-started process
gets HOME and the XDG directories in scratch (the request's environment). And Chromium's temporary
directory, where it keeps the socket and lock that allow one browser per profile and which it
leaves behind even after a clean exit, is the browser directory in scratch, named relatively
(TMPDIR=".") because an absolute path would not fit a Unix socket address under a deep scratch
root. Whatever goes wrong once the process has started, it is stopped before the error leaves.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Called by `hivemind.exoskeleton.attach` (built in the composition root). Calls into
    `hivemind.cell` (CellSession, BackgroundSpec), `.locate`, `.excerpts`, `.base`,
    `hivemind.exoskeleton.errors`, `.geometry` and, lazily, `.playwright`.

Key invariants:
    - A launch that raises has stopped everything it started, whatever the failure: a locate or
      connect error, a closed session, a crash, a timeout or a cancellation.
    - The browser's DevTools port listens on loopback only, and nothing here logs it.
    - Chromium's sandbox is dropped only when the request says so (`BrowserLaunch.sandbox`).

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for why the browser is a
      lease-started process driven over CDP.
    - hivemind.exoskeleton.browser.base for BrowserLauncher, BrowserLaunch and LaunchedBrowser.
    - hivemind.exoskeleton.scratch for the layout every path here comes from.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from hivemind.cell import (
    BackgroundProcess,
    BackgroundSpec,
    BackgroundStartError,
    CellSession,
    PathNotAllowedError,
    SessionClosedError,
)
from hivemind.common.logging import get_logger
from hivemind.exoskeleton.browser.base import Browser, BrowserLaunch, LaunchedBrowser
from hivemind.exoskeleton.browser.excerpts import detail
from hivemind.exoskeleton.browser.locate import ChromiumLocator
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.geometry import ScreenSize
from waggle.clock import Clock

# Chromium writes its port in well under a second (0.25 s to 0.5 s measured); thirty covers a
# cold first start on a slow Cell, the first run of a snap-packaged browser included.
DEFAULT_LAUNCH_TIMEOUT_S = 30.0
DEFAULT_NAVIGATION_TIMEOUT_S = 30.0  # One page load on a slow site; Playwright's own default.
DEFAULT_ELEMENT_TIMEOUT_S = 5.0  # How long a step waits for its element after the page reacts.
DEFAULT_WINDOW = ScreenSize(1280, 800)  # The window's size, headed or headless.
PORT_POLL_S = 0.1  # How often DevToolsActivePort is re-read while Chromium starts.
# One file read or liveness check through the session: milliseconds. Bounded so a session that
# stopped answering fails the launch instead of hanging attach.
SESSION_CALL_TIMEOUT_S = 10.0
PORT_FILE = "DevToolsActivePort"  # Chromium writes the port it chose here, in the profile.
PROFILE_DIR = "profile"  # The profile's directory inside the layout's browser directory.
LOG_FILE = "chromium.log"  # Chromium's stdout and stderr, inside the browser directory.
LOG_TAIL_BYTES = 4_096  # How much of a failed browser's log is read to say why it stopped.
EXTRA_NAME = "hivemind[browser]"  # The optional extra that installs Playwright.
# Switches every lease's browser gets, each group for one reason.
QUIET_FLAGS = (
    # No welcome page and no default-browser question: nothing a task did not ask for on screen.
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-search-engine-choice-screen",
    # Never touch the operator's keyring (Linux) or keychain (macOS) for the profile's secrets.
    "--password-store=basic",
    "--use-mock-keychain",
    # Keep the browser's own background traffic and work to what these switches can stop.
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-sync",
    "--disable-default-apps",
    "--disable-extensions",
    "--disable-breakpad",
    # A container's /dev/shm is 64 MB, which a heavy page exhausts; Chromium uses TMPDIR instead.
    "--disable-dev-shm-usage",
)

__all__ = [
    "DEFAULT_ELEMENT_TIMEOUT_S",
    "DEFAULT_LAUNCH_TIMEOUT_S",
    "DEFAULT_NAVIGATION_TIMEOUT_S",
    "DEFAULT_WINDOW",
    "EXTRA_NAME",
    "LOG_FILE",
    "PORT_FILE",
    "PROFILE_DIR",
    "QUIET_FLAGS",
    "CdpConnector",
    "ChromiumLauncher",
    "ChromiumSettings",
    "chromium_spec",
    "parse_port",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ChromiumSettings:
    """How a ChromiumLauncher starts and drives a browser; one value per composition root."""

    launch_timeout_s: float = DEFAULT_LAUNCH_TIMEOUT_S  # Start to DevTools port; > 0.
    window: ScreenSize = DEFAULT_WINDOW  # The browser window's size.
    navigation_timeout_s: float = DEFAULT_NAVIGATION_TIMEOUT_S  # One page load; > 0.
    element_timeout_s: float = DEFAULT_ELEMENT_TIMEOUT_S  # One wait for an element; > 0.


class CdpConnector(Protocol):
    """Connect a Browser to the Chromium serving DevTools at `endpoint`."""

    async def __call__(self, endpoint: str, file_roots: tuple[Path, ...]) -> Browser:
        """Connect and return the Browser, loading file URLs only inside `file_roots`.

        Raises AttachError when connecting, or guarding the browser's file access, fails.
        """
        ...


class ChromiumLauncher:
    """Start the lease's Chromium through its Cell's session and connect a Browser to it."""

    def __init__(
        self,
        clock: Clock,
        *,
        locator: ChromiumLocator | None = None,
        connector: CdpConnector | None = None,
        settings: ChromiumSettings | None = None,
    ) -> None:
        """Build a launcher.

        Args:
            clock: Paces the wait for the DevTools port and stamps every screenshot.
            locator: Finds the browser on the Cell; None reads the running host.
            connector: Connects to the started browser; None uses Playwright, imported at launch.
            settings: Timeouts and window size; None takes the defaults.
        """
        self._clock = clock
        self._locator = locator if locator is not None else ChromiumLocator()
        self._connector = connector
        self._settings = settings if settings is not None else ChromiumSettings()

    async def launch(self, session: CellSession, request: BrowserLaunch) -> LaunchedBrowser:
        """Start a Chromium for the lease and return a connected Browser; see BrowserLauncher.

        Raises:
            AttachError: Playwright is missing, no browser was found, it could not start, it
                exited or never opened its port within the launch timeout, or connecting failed.
        """
        # Everything that can fail before a process exists is checked first, so a Hive without
        # Playwright or without a browser never starts one it cannot drive.
        connect = self._connector or _playwright_connector(self._clock, self._settings)
        executable = await self._locator.locate(session)
        port_file = request.layout.browser_dir / PROFILE_DIR / PORT_FILE
        await _forget_stale_port(session, port_file)
        started_at = self._clock.monotonic()
        process = await _start(session, chromium_spec(executable.path, request, self._settings))
        launched = False
        try:
            port = await self._wait_for_port(session, process, port_file)
            browser = await connect(f"http://127.0.0.1:{port}", request.file_roots)
            launched = True
        finally:
            # Any failure, a cancellation included: the lease must not keep a half-started browser.
            if not launched:
                await session.stop(process)
        took_s = round(self._clock.monotonic() - started_at, 3)
        log.info(
            "exoskeleton.browser_launched", pid=process.pid, headed=request.headed, took_s=took_s
        )
        return LaunchedBrowser(browser=browser, processes=(process,))

    async def _wait_for_port(
        self, session: CellSession, process: BackgroundProcess, port_file: Path
    ) -> int:
        """Poll for the port Chromium writes, failing fast if it exits and at the deadline."""
        timeout_s = self._settings.launch_timeout_s
        deadline = self._clock.monotonic() + timeout_s
        while True:
            port = await _read_port(session, port_file)
            if port is not None:
                return port
            # A browser that crashed on start says why in its log; waiting on would only time out.
            if not await _still_running(session, process):
                reason = await _log_tail(session, process)
                raise AttachError(f"Chromium exited before it was ready: {reason}")
            if self._clock.monotonic() >= deadline:
                reason = await _log_tail(session, process)
                raise AttachError(f"Chromium opened no DevTools port within {timeout_s}s: {reason}")
            await self._clock.sleep(PORT_POLL_S)


def chromium_spec(
    executable: str, request: BrowserLaunch, settings: ChromiumSettings
) -> BackgroundSpec:
    """Build the command that starts one lease's Chromium.

    Args:
        executable: The browser, as the locator found it.
        request: Where its files go, headed or not, sandboxed or not, its environment.
        settings: The window size.

    Returns:
        The BackgroundSpec: profile, log and working directory in the layout's browser directory.
    """
    browser_dir = request.layout.browser_dir
    window = settings.window
    argv = (
        executable,
        f"--user-data-dir={browser_dir / PROFILE_DIR}",
        # Port 0: Chromium picks a free port itself and writes it to DevToolsActivePort.
        "--remote-debugging-port=0",
        "--remote-debugging-address=127.0.0.1",
        f"--window-size={window.width},{window.height}",
        *QUIET_FLAGS,
        # Headed, the window fills the display from its corner; headless, there is no window.
        *(("--window-position=0,0",) if request.headed else ("--headless=new",)),
        # SAFETY: only where the composition root says the Cell is itself the sandbox or the Hive
        # runs as root, which Chromium's own sandbox cannot start under (ADR-0031).
        *(() if request.sandbox else ("--no-sandbox",)),
        "about:blank",
    )
    # TMPDIR is relative to the working directory, the browser directory (module docstring).
    environment = {**request.environment, "TMPDIR": "."}
    return BackgroundSpec(
        argv=argv, cwd=browser_dir, env=environment, log_path=browser_dir / LOG_FILE
    )


def parse_port(data: bytes) -> int | None:
    """Read the port from a DevToolsActivePort file's first line.

    Args:
        data: The file's contents: the port, a newline, the browser target's path.

    Returns:
        The port, or None while the file is still being written or holds no port.
    """
    # Only a finished first line counts: "322" read mid-write is not port 32245.
    first, newline, _ = data.partition(b"\n")
    if not newline or not first.strip().isdigit():
        return None
    port = int(first.strip())
    return port if 0 < port < 65_536 else None


def _playwright_connector(clock: Clock, settings: ChromiumSettings) -> CdpConnector:
    """Import the Playwright backend and wrap its connect, or say which extra is missing."""
    try:
        # Imported here, not at the top: Playwright is the optional hivemind[browser] extra and
        # only a launch needs it (the import-linter contract keeps it to that one package).
        from hivemind.exoskeleton.browser import playwright as driver
    except ImportError as error:
        raise AttachError(
            f"the browser fast path needs Playwright, which is not installed: install {EXTRA_NAME}"
        ) from error
    timeouts = driver.PlaywrightTimeouts(
        navigation_s=settings.navigation_timeout_s, element_s=settings.element_timeout_s
    )

    async def connect(endpoint: str, file_roots: tuple[Path, ...]) -> Browser:
        """Attach Playwright to the browser at `endpoint` (seconds at most; see its timeouts)."""
        return await driver.connect_browser(endpoint, clock, timeouts, file_roots)

    return connect


async def _forget_stale_port(session: CellSession, port_file: Path) -> None:
    """Remove a port file an earlier browser in this profile left, so no dead port is read."""
    try:
        async with asyncio.timeout(SESSION_CALL_TIMEOUT_S):
            # The usual case is a fresh profile with no port file in it at all.
            with contextlib.suppress(FileNotFoundError):
                await session.delete_file(port_file)
    except (SessionClosedError, PathNotAllowedError, TimeoutError) as error:
        raise AttachError(f"could not prepare the browser's profile: {error}") from error


async def _start(session: CellSession, spec: BackgroundSpec) -> BackgroundProcess:
    """Start Chromium through the session, turning a refusal into an AttachError."""
    try:
        return await session.start(spec)
    except (BackgroundStartError, SessionClosedError, PathNotAllowedError) as error:
        raise AttachError(f"could not start {spec.argv[0]}: {error}") from error


async def _read_port(session: CellSession, port_file: Path) -> int | None:
    """Return the port Chromium wrote, or None when it has not written one yet."""
    try:
        # One small file read through the session: milliseconds, bounded all the same.
        async with asyncio.timeout(SESSION_CALL_TIMEOUT_S):
            data = await session.get_file(port_file)
    except FileNotFoundError:
        return None  # Not written yet: Chromium is still starting.
    except (SessionClosedError, PathNotAllowedError, TimeoutError) as error:
        raise AttachError(f"could not read Chromium's DevTools port: {error}") from error
    return parse_port(data)


async def _still_running(session: CellSession, process: BackgroundProcess) -> bool:
    """Return whether the browser process is alive, bounded like every session call here."""
    try:
        async with asyncio.timeout(SESSION_CALL_TIMEOUT_S):
            return await session.is_running(process)
    except TimeoutError as error:
        raise AttachError("the Cell's session did not say whether Chromium is running") from error


async def _log_tail(session: CellSession, process: BackgroundProcess) -> str:
    """Return the end of the browser's log as one short, URL-free line, or say there is none."""
    if process.log_path is None:
        return "it kept no log"
    try:
        # One small file read through the session, bounded like every other call here.
        async with asyncio.timeout(SESSION_CALL_TIMEOUT_S):
            data = await session.get_file(process.log_path)
    except (FileNotFoundError, SessionClosedError, PathNotAllowedError, TimeoutError) as error:
        # The launch is failing anyway; a log it cannot read only makes the message vaguer.
        log.debug("exoskeleton.browser_log_unread", reason=type(error).__name__)
        return "its log could not be read"
    text = data[-LOG_TAIL_BYTES:].decode("utf-8", errors="replace")
    return detail(text, from_end=True) or "its log is empty"
