"""Provide one launched browser per implementation, for the browser contract suite.

The browser contract suite (roadmap step 6.11) states each clause of `Browser` and `BrowserLauncher`
once and runs it over two implementations: the fake (`FakeBrowserHarness`: `FakeBrowserLauncher`
serving `login_site()` over a `FakeSession`) and the real one (`RealBrowserHarness`:
`ChromiumLauncher` starting a real Chromium through a real `LocalProcessSession`, driven by
Playwright over CDP, against the static fixture site under `tests/fixtures/sites/login/` opened as
file:// URLs). Both serve the same site, so a clause names its pages ("login", "welcome", "long",
"missing") and each harness turns a name into its own URL. Each harness also answers the
launcher's clauses its own way: stopping what a launch started, launching a browser that never
becomes ready, and saying what is still running afterwards (the fake asks its FakeSession; the
real one looks for any process whose command line mentions the lease's scratch directory, which
catches Chromium's crash handler too, since it runs in a process group of its own).

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Used by
    `contracts.test_browser_contract` and by the Playwright backend's unit tests.

Key invariants:
    - The real harness works in a short scratch directory (`short_scratch`), never pytest's
      tmp_path: on POSIX it is under /tmp, because Chromium's singleton socket must fit a Unix
      socket address; Windows has no such socket and no /tmp, so there it is the system's own
      temporary directory.
    - `processes_mentioning` reads every process's command line, from /proc on Linux and from
      the process table (through PowerShell) on Windows, so a browser that outlived its lease
      is found on both.
    - Everything a harness started is stopped in `close`, and its scratch is removed.

See Also:
    - hivemind.exoskeleton.browser.fake for the fake site and browser.
    - hivemind.exoskeleton.browser.launch for the real launcher.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest
from builders.cells import make_hive_stand_releaser, make_real_cell_lease

from hivemind.cell import BackgroundProcess, CellSession
from hivemind.cell.fake import FakeSession
from hivemind.cell.local.quota import ScratchQuota
from hivemind.cell.local.session import LocalProcessSession
from hivemind.exoskeleton.browser import (
    Browser,
    BrowserLaunch,
    BrowserLauncher,
    CdpConnector,
    ChromiumLauncher,
    ChromiumLocator,
    ChromiumSettings,
    FakeBrowserLauncher,
    HostScopes,
    login_site,
)
from hivemind.exoskeleton.browser.fake import FIXTURE_ORIGIN
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.scratch import ScratchLayout
from hivemind.guard import CapabilitySet
from waggle.clock import FakeClock, SystemClock

SITE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "sites" / "login"
# Short, so a clause about a missing element spends little time waiting for it.
ELEMENT_TIMEOUT_S = 2.0
_QUOTA_BYTES = 1 << 30  # A profile is tens of megabytes; the quota is not under test here.
_UNREADY_TIMEOUT_S = 0.001  # Far shorter than any Chromium takes to open its port (0.25 s+).
_SETTLE_S = 10.0  # How long a stopped browser's own crash handler may take to follow it out.
_POLL_S = 0.1  # How often the process table is re-read while waiting for that.
# Where POSIX scratch goes: short, so a Unix socket path under it fits in 108 bytes.
_POSIX_SCRATCH_ROOT = "/tmp"  # noqa: S108  # SAFETY: mkdtemp makes a private 0700 directory.
_PROCESS_LIST_TIMEOUT_S = 60.0  # One PowerShell process query: about a second, slow when cold.
# Every process on Windows, one per line: its id, a tab, its command line (blank when unreadable).
_WINDOWS_PROCESS_QUERY = (
    'Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }'
)

__all__ = [
    "ELEMENT_TIMEOUT_S",
    "HARNESSES",
    "SITE_DIR",
    "BrowserHarness",
    "FakeBrowserHarness",
    "OpenBrowser",
    "RealBrowserHarness",
    "processes_mentioning",
    "short_scratch",
]


@dataclass
class OpenBrowser:
    """One launched browser under test: the Browser, its session, and its site's addresses."""

    browser: Browser
    session: CellSession
    processes: tuple[BackgroundProcess, ...]  # What the launch started.
    url: Callable[[str], str]  # A page's name ("login", "welcome", "long", "missing") to its URL.


class BrowserHarness(Protocol):
    """Launches a browser for one test and cleans up after it."""

    name: str

    def missing(self) -> str | None:
        """Return why this harness cannot run here, or None when it can."""
        ...

    async def open(self) -> OpenBrowser:
        """Launch a fresh browser on about:blank; skip the test if no browser can be found."""
        ...

    async def launch_unready(self) -> None:
        """Launch a browser that never becomes ready; the launcher's AttachError propagates."""
        ...

    async def stop_launched(self) -> None:
        """Stop every process the launches so far started, as detach does."""
        ...

    async def leftovers(self) -> tuple[str, ...]:
        """Describe every process of this harness's launches still running (none, ideally)."""
        ...

    async def close(self) -> None:
        """Disconnect, stop everything and remove the scratch directory."""
        ...


class FakeBrowserHarness:
    """The fake: FakeBrowserLauncher serving login_site() over a FakeSession. Always available."""

    name = "fake"

    def missing(self) -> str | None:
        """Always available: the fake needs nothing installed."""
        return None

    async def open(self) -> OpenBrowser:
        """Launch a FakeBrowser on login_site() over a fresh FakeSession."""
        self._scratch = Path(tempfile.mkdtemp(prefix="hm-fb-"))
        self._session = FakeSession(self._scratch, FakeClock())
        self._request = _request(self._scratch)
        launched = await FakeBrowserLauncher(login_site(), FakeClock()).launch(
            self._session, self._request
        )
        self._opened = OpenBrowser(
            launched.browser,
            self._session,
            launched.processes,
            lambda page: f"{FIXTURE_ORIGIN}/{page}",
        )
        return self._opened

    async def launch_unready(self) -> None:
        """Launch with a FakeBrowserLauncher scripted never to be ready."""
        launcher = FakeBrowserLauncher(login_site(), FakeClock(), ready=False)
        await launcher.launch(self._session, self._request)

    async def stop_launched(self) -> None:
        """Stop the stand-in processes the launch started."""
        for process in self._opened.processes:
            await self._session.stop(process)

    async def leftovers(self) -> tuple[str, ...]:
        """Name every simulated process still running."""
        return tuple(f"pid {pid}" for pid in self._session.running_pids)

    async def close(self) -> None:
        """Close the browser and the session, and remove the scratch directory."""
        await self._opened.browser.close()
        await self._session.close()
        shutil.rmtree(self._scratch, ignore_errors=True)


class RealBrowserHarness:
    """A real Chromium started by ChromiumLauncher on a real LocalProcessSession, over file://."""

    name = "real"

    def __init__(self, connector: CdpConnector | None = None) -> None:
        """Build the harness; `connector` replaces Playwright's default connect when given."""
        self._connector = connector

    def missing(self) -> str | None:
        """Say so when Playwright is not installed; a missing browser skips in open."""
        if importlib.util.find_spec("playwright") is None:
            return "Playwright is not installed here (the hivemind[browser] extra)"
        return None

    async def open(self) -> OpenBrowser:
        """Start a real Chromium through a LocalProcessSession, or skip without one."""
        clock = SystemClock()
        self._scratch = short_scratch()
        lease = make_real_cell_lease(
            self._scratch, clock=clock, releaser=make_hive_stand_releaser(clock=clock)
        )
        self._session = LocalProcessSession(lease, ScratchQuota(quota_bytes=_QUOTA_BYTES), clock)
        self._request = _request(self._scratch)
        self._locator = ChromiumLocator()
        self._clock = clock
        self._opened: OpenBrowser | None = None
        try:
            self._opened = await self._launch()
        finally:
            # A skip or a failed launch never reaches the fixture's close: clean up here.
            if self._opened is None:
                await self._session.close()
                shutil.rmtree(self._scratch, ignore_errors=True)
        return self._opened

    async def _launch(self) -> OpenBrowser:
        """Find a browser (skipping the test without one), then launch it for the fixture site."""
        try:
            await self._locator.locate(self._session)
        except AttachError as error:
            pytest.skip(f"no browser to run the real harness with: {error.reason}")
        settings = ChromiumSettings(element_timeout_s=ELEMENT_TIMEOUT_S)
        launched = await self._launcher(settings).launch(self._session, self._request)
        return OpenBrowser(launched.browser, self._session, launched.processes, _fixture_url)

    async def launch_unready(self) -> None:
        """Launch with a timeout no Chromium can meet, so it never gets ready."""
        settings = ChromiumSettings(launch_timeout_s=_UNREADY_TIMEOUT_S)
        await self._launcher(settings).launch(self._session, self._request)

    async def stop_launched(self) -> None:
        """Stop the processes the launch started, as detach does."""
        if self._opened is not None:
            for process in self._opened.processes:
                await self._session.stop(process)

    async def leftovers(self) -> tuple[str, ...]:
        """Name every live process mentioning the scratch directory."""
        # Chromium's crash handler runs in a process group of its own and leaves once the browser
        # is gone, so allow it a moment before calling anything a leftover.
        deadline = time.monotonic() + _SETTLE_S
        # Every path a lease-started browser is given is under it, spelled the platform's way.
        inside = f"{self._scratch}{os.sep}"
        found = processes_mentioning(inside)
        while found and time.monotonic() < deadline:
            await asyncio.sleep(_POLL_S)
            found = processes_mentioning(inside)
        return found

    async def close(self) -> None:
        """Disconnect, close the session (killing what is left) and remove scratch."""
        if self._opened is not None:
            await self._opened.browser.close()
        await self._session.close()
        shutil.rmtree(self._scratch, ignore_errors=True)

    def _launcher(self, settings: ChromiumSettings) -> BrowserLauncher:
        """Build a ChromiumLauncher with this harness's locator and `settings`."""
        return ChromiumLauncher(
            self._clock, locator=self._locator, connector=self._connector, settings=settings
        )


def short_scratch(prefix: str = "hm-") -> Path:
    """Make a fresh, private scratch directory with a short path; see the module's invariants.

    Args:
        prefix: The directory name's start; `hm-` is what the orphan sweep looks for.

    Returns:
        A new directory under /tmp on POSIX, under the system's temporary directory on Windows.
        The caller removes it.
    """
    root = _POSIX_SCRATCH_ROOT if os.name == "posix" else None
    return Path(tempfile.mkdtemp(prefix=prefix, dir=root))


def processes_mentioning(text: str) -> tuple[str, ...]:
    """Describe every live process whose command line contains `text` (Linux and Windows).

    Elsewhere (macOS) nothing is found: no real browser harness has run there.
    """
    if os.name == "nt":  # Not sys.platform: mypy would drop the other branch per platform.
        return _windows_processes_mentioning(text)
    if not Path("/proc").is_dir():
        return ()
    found = []
    # Every process: its command line, and its state (a zombie has already exited).
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            command = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            state = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except (OSError, IndexError):
            continue  # It exited while being read.
        if text in command and state != "Z":
            found.append(f"pid {entry.name}: {command[:80]}")
    return tuple(found)


def _windows_processes_mentioning(text: str) -> tuple[str, ...]:
    """Describe every Windows process whose command line contains `text`, from the process table.

    Windows has no /proc; its process table gives every process's command line through WMI.
    The query's own PowerShell process is left out: its command line holds the query, not `text`.
    """
    result = subprocess.run(  # noqa: S603  # SAFETY: a fixed argument list, never a shell.
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_PROCESS_QUERY],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=_PROCESS_LIST_TIMEOUT_S,
        check=True,
    )
    found = []
    for line in result.stdout.splitlines():
        pid, _, command = line.partition("\t")
        if text in command:
            found.append(f"pid {pid}: {command[:80]}")
    return tuple(found)


def _request(scratch: Path) -> BrowserLaunch:
    """Build the request attach would: headless, the layout's directories made, HOME in scratch.

    The sandbox is off for the same reason a composition root turns it off: Chromium's own
    sandbox cannot start as root, nor where unprivileged user namespaces are blocked, which
    covers this suite's containers and CI runners; the flag's handling is unit-tested instead.
    The file roots are scratch, as attach names them, plus the fixture site's directory, which
    this harness opens as file:// URLs in place of pages a bee wrote into scratch. The hosts are
    `net:loopback` alone, as an operator would grant a Real Cell's bee to reach a site it serves
    itself, so a test serving pages on loopback works and every other host is blocked.
    """
    layout = ScratchLayout.under(scratch)
    for directory in layout.directories():
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return BrowserLaunch(
        layout=layout,
        headed=False,
        sandbox=False,
        environment=layout.home_environment(),
        file_roots=(scratch, SITE_DIR),
        hosts=HostScopes(CapabilitySet.parse("net:loopback")),
    )


def _fixture_url(page: str) -> str:
    """Return the file:// URL of one fixture page ("missing" names one that does not exist)."""
    return (SITE_DIR / f"{page}.html").as_uri()


# Every test gets a fresh harness from its factory, so no state crosses tests.
HARNESSES: tuple[Callable[[], BrowserHarness], ...] = (FakeBrowserHarness, RealBrowserHarness)
