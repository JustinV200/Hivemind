"""Find a Chromium-family browser on a Cell: ChromiumLocator, and the host facts it reads.

The browser fast path starts a Chromium the Cell already has (ADR-0031): a Playwright-managed
build, a Chromium or Chrome on the PATH, or on Windows the Edge every install carries. Candidates
are tried in that order. Playwright's builds come first, newest revision first, from
PLAYWRIGHT_BROWSERS_PATH or Playwright's default cache directory, and any `chromium-*` revision
counts, not only the one the installed Playwright package expects, because connecting over CDP
works with any recent Chromium. Listing that cache needs a directory listing, which a CellSession
(the Cell's terminal) does not offer, so it happens bee-side; that is sound because every Cell
kind in phase 6 (inside a Virtual Cell, the Linux Hive Stand, the Windows Hive Stand) shares the
bee's own filesystem. The candidate chosen is still verified through the session before it is
used: `--version` on Linux and macOS, and `where` on Windows, where running a browser with
`--version` would open a window on the operator's desktop instead of printing a version. The host
facts (which system, its environment, its home) are read once, in `HostPlatform.current`, so a
test can hand the locator any host it likes.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.exoskeleton.browser`.
    Called by `browser.launch.ChromiumLauncher` before it starts the browser. Calls into
    `hivemind.cell` (CellSession, run), `hivemind.exoskeleton.errors` and `.excerpts` only.

Key invariants:
    - A candidate is returned only after a command run through the session confirmed it.
    - A candidate that is missing, fails or hangs is skipped, never an error; only running out of
      candidates (or a closed session) is.
    - Environment variables here are facts about where browsers are installed, never Hive
      configuration (codingrules section 13 keeps HIVEMIND_* settings in manifest/env.py).

See Also:
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for "found by the browser
      locator".
    - hivemind.exoskeleton.browser.launch for the launcher that starts what this finds.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from hivemind.cell import CellSession, CommandTimeoutError, ExecSpec, SessionClosedError, run
from hivemind.common.logging import get_logger
from hivemind.exoskeleton.browser.excerpts import detail
from hivemind.exoskeleton.errors import AttachError

# One `--version` or `where`: milliseconds normally, but a snap-packaged Chromium's first start
# takes seconds; a candidate slower than this is skipped rather than waited on.
PROBE_TIMEOUT_S = 10.0
PLAYWRIGHT_BROWSERS_ENV = "PLAYWRIGHT_BROWSERS_PATH"  # Where Playwright was told to keep browsers.
# Names a Chromium-family browser installs on the PATH, in the order they are preferred.
PATH_NAMES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "microsoft-edge",
)
WINDOWS = "win32"  # sys.platform on Windows.
MACOS = "darwin"  # sys.platform on macOS.
_REVISION = re.compile(r"^chromium-(\d+)$")  # One Playwright build's directory: "chromium-1194".
_VERSION = re.compile(r"\d+\.\d+")  # Every Chromium's --version line has one: "Chromium 141.0...".
_CFT_APP = ("Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing")
# Where the executable sits inside one Playwright build directory: the Chrome for Testing layouts
# (Playwright 1.57 and later) first, then the Chromium layouts older revisions still use.
_BUILD_LAYOUTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "linux": (
        ("chrome-linux64", "chrome"),
        ("chrome-linux-arm64", "chrome"),
        ("chrome-linux", "chrome"),
    ),
    MACOS: (
        ("chrome-mac-arm64", *_CFT_APP),
        ("chrome-mac-x64", *_CFT_APP),
        ("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
    ),
    WINDOWS: (("chrome-win64", "chrome.exe"), ("chrome-win", "chrome.exe")),
}
# macOS keeps browsers in app bundles, never on the PATH.
_MACOS_APPS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)

__all__ = [
    "MACOS",
    "PATH_NAMES",
    "PLAYWRIGHT_BROWSERS_ENV",
    "PROBE_TIMEOUT_S",
    "WINDOWS",
    "ChromiumExecutable",
    "ChromiumLocator",
    "HostPlatform",
    "browser_candidates",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HostPlatform:
    """The facts about the bee's host the locator reads: its system, environment and home."""

    system: str  # sys.platform: "linux", "darwin" or "win32"; anything else is treated as Linux.
    environment: Mapping[str, str]  # The bee's environment variables.
    home: Path  # The bee's home directory.

    @classmethod
    def current(cls) -> HostPlatform:
        """Read this host's facts: the one place the locator reads os.environ and sys.platform.

        Returns:
            The running host's platform, environment and home directory.
        """
        return cls(system=sys.platform, environment=dict(os.environ), home=Path.home())

    def variable(self, name: str) -> str | None:
        """Return one environment variable, or None when unset or empty.

        Windows compares variable names without regard to case (`os.environ` there even
        upper-cases them), so there the lookup does too.

        Args:
            name: The variable, as its documentation spells it ("LOCALAPPDATA").

        Returns:
            Its value, or None.
        """
        value = self.environment.get(name)
        if value is None and self.system == WINDOWS:
            wanted = name.upper()
            value = next((v for k, v in self.environment.items() if k.upper() == wanted), None)
        return value or None


@dataclass(frozen=True, slots=True)
class ChromiumExecutable:
    """A browser the locator found and confirmed on the Cell."""

    path: str  # What the launcher starts: an absolute path, or a name the Cell's PATH resolves.
    version: str | None  # Its --version line; None on Windows, where it is not asked for.


class ChromiumLocator:
    """Find the Chromium-family browser a lease's browser is started from, checked on the Cell."""

    def __init__(
        self, platform: HostPlatform | None = None, probe_timeout_s: float = PROBE_TIMEOUT_S
    ) -> None:
        """Build a locator for one host.

        Args:
            platform: The host's facts; None reads the running host's (`HostPlatform.current`).
            probe_timeout_s: Seconds each verification command may take before its candidate is
                skipped. Must be > 0.
        """
        self._platform = platform if platform is not None else HostPlatform.current()
        self._probe_timeout_s = probe_timeout_s

    async def locate(self, session: CellSession) -> ChromiumExecutable:
        """Return the first candidate that verifies on the Cell.

        Latency: one short command per candidate tried, usually one or two.

        Args:
            session: The Cell's session; every verification runs through it.

        Returns:
            The browser to start.

        Raises:
            AttachError: No candidate verified, or the session closed while looking.
        """
        # Listing Playwright's cache reads the bee's own disk: blocking I/O, off the event loop.
        candidates = await asyncio.to_thread(browser_candidates, self._platform)
        for candidate in candidates:
            found = await self._verify(session, candidate)
            if found is not None:
                log.info("exoskeleton.browser_located", path=found.path, version=found.version)
                return found
        raise AttachError(
            f"no Chromium-family browser was found on the Cell ({len(candidates)} candidates "
            "checked); install Chromium, Chrome or Edge, or run `playwright install chromium`"
        )

    async def _verify(self, session: CellSession, candidate: str) -> ChromiumExecutable | None:
        """Run the platform's check for `candidate` on the Cell; None when it does not pass."""
        on_windows = self._platform.system == WINDOWS
        argv = _where(candidate) if on_windows else (candidate, "--version")
        spec = ExecSpec(argv=argv, timeout_s=self._probe_timeout_s)
        try:
            # One short command on the Cell, bounded by its own timeout; a hung one is skipped.
            completed = await run(session, spec)
        except CommandTimeoutError:
            log.debug("exoskeleton.browser_candidate_timed_out", path=candidate)
            return None
        except SessionClosedError as error:
            raise AttachError("the Cell's session closed while looking for a browser") from error
        output = completed.stdout.decode("utf-8", errors="replace").strip()
        # A missing program exits 127 through every session; any non-zero exit is a miss.
        if completed.exit_code != 0 or not output:
            return None
        if on_windows:
            # `where` prints every match, one per line; the first is what Windows would run.
            return ChromiumExecutable(path=output.splitlines()[0].strip(), version=None)
        version = output.splitlines()[0]
        return ChromiumExecutable(candidate, detail(version)) if _VERSION.search(version) else None


def browser_candidates(platform: HostPlatform) -> tuple[str, ...]:
    """Return every place a Chromium-family browser may be on `platform`, most preferred first.

    Reads the bee's own disk to list Playwright's builds (module docstring); nothing is run.

    Args:
        platform: The host to look on.

    Returns:
        Playwright's builds (newest revision first), then the PATH names, then the platform's
        well-known install locations.
    """
    return (*_playwright_builds(platform), *PATH_NAMES, *_well_known(platform))


def _playwright_builds(platform: HostPlatform) -> list[str]:
    """List every Chromium executable in Playwright's cache, newest revision first."""
    root = _playwright_root(platform)
    try:
        entries = list(root.iterdir()) if root is not None else []
    except OSError:
        return []  # No cache: Playwright never installed a browser for this user.
    revisions: list[tuple[int, Path]] = []
    # Every directory named like a Chromium build, whatever revision it is.
    for entry in entries:
        match = _REVISION.match(entry.name)
        if match is not None:
            revisions.append((int(match.group(1)), entry))
    layouts = _BUILD_LAYOUTS.get(platform.system, _BUILD_LAYOUTS["linux"])
    found: list[str] = []
    # Newest revision first; within one, whichever layout that revision was packed in.
    for _, directory in sorted(revisions, reverse=True):
        executables = (directory.joinpath(*layout) for layout in layouts)
        found.extend(str(executable) for executable in executables if executable.is_file())
    return found


def _playwright_root(platform: HostPlatform) -> Path | None:
    """Return the directory Playwright keeps its browsers in on `platform`, if one is known."""
    configured = platform.variable(PLAYWRIGHT_BROWSERS_ENV)
    if configured is not None:
        # "0" means "inside Playwright's own package", which is not a path this can list; a
        # relative path is relative to wherever Playwright's installer ran, which is unknown here.
        path = Path(configured)
        return path if configured != "0" and path.is_absolute() else None
    if platform.system == WINDOWS:
        local = platform.variable("LOCALAPPDATA")
        cache = Path(local) if local else platform.home / "AppData" / "Local"
    elif platform.system == MACOS:
        cache = platform.home / "Library" / "Caches"
    else:
        xdg = platform.variable("XDG_CACHE_HOME")
        cache = Path(xdg) if xdg else platform.home / ".cache"
    return cache / "ms-playwright"


def _well_known(platform: HostPlatform) -> tuple[str, ...]:
    """Return the platform's standard browser install locations, Edge first on Windows."""
    if platform.system == MACOS:
        return _MACOS_APPS
    if platform.system != WINDOWS:
        return ()  # Linux browsers are on the PATH, which PATH_NAMES already covers.
    home_local = PureWindowsPath(str(platform.home)) / "AppData" / "Local"
    x86 = PureWindowsPath(platform.variable("ProgramFiles(x86)") or r"C:\Program Files (x86)")
    programs = PureWindowsPath(platform.variable("ProgramFiles") or r"C:\Program Files")
    local = PureWindowsPath(platform.variable("LOCALAPPDATA") or str(home_local))
    edge = PureWindowsPath("Microsoft", "Edge", "Application", "msedge.exe")
    chrome = PureWindowsPath("Google", "Chrome", "Application", "chrome.exe")
    # Edge first: every Windows install has it, so the choice is the same on every machine.
    places = (x86 / edge, programs / edge, programs / chrome, x86 / chrome, local / chrome)
    return tuple(str(place) for place in places)


def _where(candidate: str) -> tuple[str, ...]:
    """Build the `where` command that finds `candidate` on Windows without starting it."""
    path = PureWindowsPath(candidate)
    if len(path.parts) == 1:
        return ("where", candidate)  # A bare name: `where` searches the PATH for it.
    # A full path: `where` takes the directory and the file name as "directory:name".
    return ("where", f"{path.parent}:{path.name}")
