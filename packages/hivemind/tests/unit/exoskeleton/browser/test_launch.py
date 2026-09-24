"""Unit tests for hivemind.exoskeleton.browser.launch: ChromiumLauncher over a FakeSession."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest

import hivemind.exoskeleton.browser as browser_package
from hivemind.cell import CompletedCommand, ExecSpec
from hivemind.cell.fake import FakeSession, FakeStart
from hivemind.exoskeleton.browser import Browser, BrowserLaunch, FakeBrowser, LaunchedBrowser
from hivemind.exoskeleton.browser.fake import login_site
from hivemind.exoskeleton.browser.launch import (
    EXTRA_NAME,
    LOG_FILE,
    PORT_FILE,
    PORT_POLL_S,
    PROFILE_DIR,
    ChromiumLauncher,
    ChromiumSettings,
    chromium_spec,
    parse_port,
)
from hivemind.exoskeleton.browser.locate import ChromiumLocator, HostPlatform
from hivemind.exoskeleton.errors import AttachError
from hivemind.exoskeleton.scratch import ScratchLayout
from waggle.clock import FakeClock

_CHROMIUM = "chromium"  # The one candidate the fake Cell has: the first PATH name.
_PORT_DATA = b"9222\n/devtools/browser/0c58\n"  # What Chromium writes to DevToolsActivePort.
_LAUNCH_TIMEOUT_S = 1.0  # Fake seconds; the clock only moves when a test advances it.
_REAL_GUARD_S = 5.0  # Real seconds a test may spin before it is considered hung.
_BACKEND = "hivemind.exoskeleton.browser.playwright"  # The package only a launch imports.
_SPINS = 10_000  # Event-loop turns a launch may take to reach its start (a thread hop included).


class _Connector:
    """A CdpConnector that records each endpoint and hands back a FakeBrowser, or fails."""

    def __init__(self, fails: bool = False) -> None:
        self.endpoints: list[str] = []
        self.roots: list[tuple[Path, ...]] = []
        self._fails = fails

    async def __call__(self, endpoint: str, file_roots: tuple[Path, ...]) -> Browser:
        self.endpoints.append(endpoint)
        self.roots.append(file_roots)
        if self._fails:
            raise AttachError("could not attach to the browser over CDP: refused")
        return FakeBrowser(login_site(), FakeClock(), file_roots=file_roots)


def _session(scratch: Path, *, browser: bool = True) -> FakeSession:
    """A FakeSession whose Cell has Chromium on the PATH (or no browser at all)."""
    version = CompletedCommand(exit_code=0, stdout=b"Chromium 141.0\n", stderr=b"", duration_s=0.0)
    missing = CompletedCommand(exit_code=127, stdout=b"", stderr=b"not found", duration_s=0.0)

    def respond(spec: ExecSpec) -> CompletedCommand:
        return version if browser and spec.argv[0] == _CHROMIUM else missing

    return FakeSession(scratch, FakeClock(), responder=respond)


def _request(scratch: Path, *, headed: bool = False, sandbox: bool = True) -> BrowserLaunch:
    layout = ScratchLayout.under(scratch)
    environment = {**layout.home_environment(), "DISPLAY": ":7"}
    return BrowserLaunch(
        layout=layout,
        headed=headed,
        sandbox=sandbox,
        environment=environment,
        file_roots=(scratch,),
    )


def _launcher(clock: FakeClock, scratch: Path, connector: _Connector | None) -> ChromiumLauncher:
    locator = ChromiumLocator(HostPlatform(system="linux", environment={}, home=scratch))
    settings = ChromiumSettings(launch_timeout_s=_LAUNCH_TIMEOUT_S)
    return ChromiumLauncher(clock, locator=locator, connector=connector, settings=settings)


def _port_file(request: BrowserLaunch) -> Path:
    return request.layout.browser_dir / PROFILE_DIR / PORT_FILE


async def _drive(
    launch: asyncio.Task[LaunchedBrowser],
    clock: FakeClock,
    session: FakeSession,
    port_file: Path | None,
) -> None:
    """Let the launch run, write the port once Chromium has started (if asked), and move time.

    Every wait the launcher makes is on the injected clock, so advancing it is what lets the
    poll loop go round (codingrules 14.5: no sleeping in tests).
    """
    written = False
    async with asyncio.timeout(_REAL_GUARD_S):
        while not launch.done():
            await asyncio.sleep(0)
            if port_file is not None and session.started and not written:
                await session.put_file(port_file, _PORT_DATA)
                written = True
            clock.advance(PORT_POLL_S)


# ── The command ──────────────────────────────────────────────────────────────


def test_a_headless_sandboxed_spec_keeps_every_file_in_the_browser_directory(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    browser_dir = request.layout.browser_dir

    spec = chromium_spec("/opt/chrome", request, ChromiumSettings())

    assert spec.argv[0] == "/opt/chrome"
    assert spec.argv[-1] == "about:blank"
    assert f"--user-data-dir={browser_dir / PROFILE_DIR}" in spec.argv
    assert "--remote-debugging-port=0" in spec.argv
    assert "--remote-debugging-address=127.0.0.1" in spec.argv
    assert "--headless=new" in spec.argv
    assert "--no-sandbox" not in spec.argv
    assert "--window-size=1280,800" in spec.argv
    assert spec.cwd == browser_dir
    assert spec.log_path == browser_dir / LOG_FILE
    # The lease's HOME and display, and a temporary directory relative to the browser directory.
    assert spec.env["HOME"] == str(request.layout.home)
    assert spec.env["DISPLAY"] == ":7"
    assert spec.env["TMPDIR"] == "."


def test_a_headed_spec_shows_a_window_and_drops_the_sandbox_only_when_told(
    tmp_path: Path,
) -> None:
    spec = chromium_spec(
        "chromium", _request(tmp_path, headed=True, sandbox=False), ChromiumSettings()
    )

    assert "--headless=new" not in spec.argv
    assert "--window-position=0,0" in spec.argv
    assert "--no-sandbox" in spec.argv


@pytest.mark.parametrize(
    ("data", "port"),
    [
        (b"9222\n/devtools/browser/x", 9222),
        (b" 41234 \n", 41234),
        (b"", None),
        (b"322", None),  # Read mid-write: no newline yet.
        (b"abc\n", None),
        (b"0\n", None),
        (b"70000\n", None),
    ],
)
def test_parse_port_trusts_only_a_finished_first_line(data: bytes, port: int | None) -> None:
    assert parse_port(data) == port


# ── Launching ────────────────────────────────────────────────────────────────


async def test_launch_starts_chromium_and_connects_to_the_port_it_wrote(tmp_path: Path) -> None:
    clock, session, connector = FakeClock(), _session(tmp_path), _Connector()
    request = _request(tmp_path)
    # A port file left by an earlier browser in this profile must never be read.
    await session.put_file(_port_file(request), b"1111\n/devtools/browser/old\n")
    launch = asyncio.create_task(_launcher(clock, tmp_path, connector).launch(session, request))

    await _drive(launch, clock, session, _port_file(request))
    launched = await launch

    assert connector.endpoints == ["http://127.0.0.1:9222"]
    assert connector.roots == [(tmp_path,)]  # The request's file roots reach the connection.
    assert isinstance(launched.browser, FakeBrowser)
    assert [process.argv[0] for process in launched.processes] == [_CHROMIUM]
    assert session.running_pids == tuple(process.pid for process in launched.processes)


async def test_a_browser_that_never_opens_its_port_is_stopped_before_attach_fails(
    tmp_path: Path,
) -> None:
    clock, session = FakeClock(), _session(tmp_path)
    launcher = _launcher(clock, tmp_path, _Connector())
    launch = asyncio.create_task(launcher.launch(session, _request(tmp_path)))

    await _drive(launch, clock, session, port_file=None)

    with pytest.raises(AttachError, match=f"no DevTools port within {_LAUNCH_TIMEOUT_S}s"):
        await launch
    assert len(session.started) == 1
    assert session.running_pids == ()


async def test_a_browser_that_exits_on_start_fails_fast_with_its_log_tail(tmp_path: Path) -> None:
    clock, session = FakeClock(), _session(tmp_path)
    log = b"[1:1:ERROR] Missing X server at https://secret.test/?token=abc\n"
    session.script_start(_CHROMIUM, FakeStart(log=log, running=False))
    launch = asyncio.create_task(
        _launcher(clock, tmp_path, _Connector()).launch(session, _request(tmp_path))
    )

    await _drive(launch, clock, session, port_file=None)

    # "exited", not "no DevTools port within": it failed on the crash, not at the deadline.
    with pytest.raises(AttachError, match="exited before it was ready") as raised:
        await launch
    assert "Missing X server at <url>" in str(raised.value)
    assert "token" not in str(raised.value)


async def test_a_failed_connect_stops_the_browser(tmp_path: Path) -> None:
    clock, session, connector = FakeClock(), _session(tmp_path), _Connector(fails=True)
    request = _request(tmp_path)
    launch = asyncio.create_task(_launcher(clock, tmp_path, connector).launch(session, request))

    await _drive(launch, clock, session, _port_file(request))

    with pytest.raises(AttachError, match="over CDP"):
        await launch
    assert session.running_pids == ()


async def test_a_cancelled_launch_stops_the_browser(tmp_path: Path) -> None:
    clock, session = FakeClock(), _session(tmp_path)
    launch = asyncio.create_task(
        _launcher(clock, tmp_path, _Connector()).launch(session, _request(tmp_path))
    )
    # Let the launch run until Chromium has started and it is waiting for the port.
    for _ in range(_SPINS):
        if session.started:
            break
        await asyncio.sleep(0)

    launch.cancel()

    with pytest.raises(asyncio.CancelledError):
        await launch
    assert session.running_pids == ()


async def test_no_browser_on_the_cell_starts_nothing(tmp_path: Path) -> None:
    session = _session(tmp_path, browser=False)

    with pytest.raises(AttachError, match="no Chromium-family browser"):
        await _launcher(FakeClock(), tmp_path, _Connector()).launch(session, _request(tmp_path))
    assert session.started == ()


async def test_a_start_the_session_refuses_is_an_attach_error(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.script_start(_CHROMIUM, FakeStart(fails="Permission denied"))

    with pytest.raises(AttachError, match="could not start chromium"):
        await _launcher(FakeClock(), tmp_path, _Connector()).launch(session, _request(tmp_path))


async def test_a_closed_session_is_an_attach_error_before_anything_starts(tmp_path: Path) -> None:
    session = _session(tmp_path)
    await session.close()

    with pytest.raises(AttachError):
        await _launcher(FakeClock(), tmp_path, _Connector()).launch(session, _request(tmp_path))
    assert session.started == ()


async def test_without_playwright_the_launch_names_the_extra_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: make the backend unimportable, as it is where the extra is not installed. An
    # earlier test may have imported everything already, so every Playwright module is blocked
    # (a cached `playwright.async_api` would otherwise import without its parent), and the
    # backend's modules and the package attribute `from ... import playwright` reads both go.
    for name in ["playwright", *(n for n in sys.modules if n.startswith("playwright."))]:
        monkeypatch.setitem(sys.modules, name, None)
    for name in [name for name in sys.modules if name.startswith(_BACKEND)]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.delattr(browser_package, "playwright", raising=False)
    session = _session(tmp_path)
    launcher = _launcher(FakeClock(), tmp_path, connector=None)

    # Bounded in real time: were the backend importable after all, the launch would wait on the
    # fake clock for a port no one writes, and this must fail rather than hang.
    async with asyncio.timeout(_REAL_GUARD_S):
        with pytest.raises(AttachError, match=re.escape(EXTRA_NAME)):
            await launcher.launch(session, _request(tmp_path))
    assert session.started == ()
