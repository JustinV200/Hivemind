"""Provide FakeBrowserLauncher: a BrowserLauncher that starts a stand-in process and a FakeBrowser.

A launcher's contract is about the process as much as the browser: the process is started through
the Cell's session so the lease knows it, it appears in `LaunchedBrowser.processes` so detach can
stop it, and a launch that fails has stopped it before raising (ADR-0031). The fake keeps all of
that and fakes only the browser: it starts a stand-in program (`FAKE_BROWSER_PROGRAM`) through the
session it is given, which on a `FakeSession` is a simulated background process, and hands back a
`FakeBrowser` over its site. Told the browser never becomes ready, it stops the process and raises
`AttachError`, which is what lets the contract suite state that clause for every launcher. It keeps
every request it was given, so a test can see what attach asked for.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside
    `hivemind.exoskeleton.browser.fake`. Used by tests and demos in place of
    `browser.launch.ChromiumLauncher`. Calls into `hivemind.cell` (CellSession, BackgroundSpec),
    `.browser`, `.site`, `browser.base` and `hivemind.exoskeleton.errors`.

Key invariants:
    - A launch that raises has stopped the process it started.
    - The stand-in's profile and log paths are the same ones ChromiumLauncher uses, in scratch.

See Also:
    - hivemind.exoskeleton.browser.launch for the real launcher.
    - hivemind.cell.fake for FakeSession, whose simulated processes this starts.
"""

from __future__ import annotations

from hivemind.cell import (
    BackgroundSpec,
    BackgroundStartError,
    CellSession,
    PathNotAllowedError,
    SessionClosedError,
)
from hivemind.exoskeleton.browser.base import BrowserLaunch, LaunchedBrowser
from hivemind.exoskeleton.browser.fake.browser import FakeBrowser
from hivemind.exoskeleton.browser.fake.site import FakeSite
from hivemind.exoskeleton.errors import AttachError
from waggle.clock import Clock

FAKE_BROWSER_PROGRAM = "fake-chromium"  # The stand-in program a FakeSession pretends to start.
FAKE_LOG_FILE = "chromium.log"  # Where the stand-in's output goes, as for the real browser.

__all__ = ["FAKE_BROWSER_PROGRAM", "FAKE_LOG_FILE", "FakeBrowserLauncher"]


class FakeBrowserLauncher:
    """Start a stand-in browser process through the session and return a FakeBrowser.

    Owns mutable state (codingrules 8.5): the requests it has been given, in order.
    """

    def __init__(self, site: FakeSite, clock: Clock, *, ready: bool = True) -> None:
        """Build a launcher for one site.

        Args:
            site: What every browser it launches serves.
            clock: Stamps the browsers' screenshots.
            ready: False makes every launch fail as a browser that never became ready would.
        """
        self._site = site
        self._clock = clock
        self._ready = ready
        self._launches: list[BrowserLaunch] = []

    @property
    def launches(self) -> tuple[BrowserLaunch, ...]:
        """Every request `launch` was given, oldest first."""
        return tuple(self._launches)

    async def launch(self, session: CellSession, request: BrowserLaunch) -> LaunchedBrowser:
        """Start the stand-in and return a FakeBrowser on about:blank; see BrowserLauncher.

        Raises:
            AttachError: The session refused the start, or the launcher was built not ready (the
                stand-in is stopped first).
        """
        self._launches.append(request)
        browser_dir = request.layout.browser_dir
        spec = BackgroundSpec(
            argv=(FAKE_BROWSER_PROGRAM, f"--user-data-dir={browser_dir / 'profile'}"),
            cwd=browser_dir,
            env=dict(request.environment),
            log_path=browser_dir / FAKE_LOG_FILE,
        )
        try:
            process = await session.start(spec)
        except (BackgroundStartError, SessionClosedError, PathNotAllowedError) as error:
            raise AttachError(f"could not start {FAKE_BROWSER_PROGRAM}: {error}") from error
        if not self._ready:
            # As the real launcher does: nothing half-started outlives a failed launch.
            await session.stop(process)
            raise AttachError(f"{FAKE_BROWSER_PROGRAM} never became ready (scripted)")
        return LaunchedBrowser(browser=FakeBrowser(self._site, self._clock), processes=(process,))
