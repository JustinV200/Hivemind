"""Unit tests for hivemind.exoskeleton.browser.playwright.storage, on a real Chromium.

What a checkpoint covers only exists in a real browser: cookies (file:// pages get none, so these
tests serve the fixture site over http as well), the local storage of an origin the page has left
(read in a blank tab whose requests never reach the server), and the one storage area file://
pages share (reached through a directory listing). The site is served from this process by the
standard library's HTTP server on loopback, under two origins: 127.0.0.1 and localhost.
"""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field

import pytest

pytest.importorskip("playwright.async_api")

from contracts.browser_harness import SITE_DIR, OpenBrowser, RealBrowserHarness

from hivemind.exoskeleton.browser import Browser
from hivemind.exoskeleton.browser.fake import LOGIN_PASSWORD, LOGIN_USERNAME, SESSION_KEY
from hivemind.exoskeleton.browser.playwright import PlaywrightBrowser, PlaywrightTimeouts
from hivemind.exoskeleton.browser.playwright.calls import PlaywrightCalls
from hivemind.exoskeleton.browser.playwright.connect import CdpConnection, attach_cdp
from hivemind.exoskeleton.browser.playwright.storage import CheckpointStorage
from hivemind.exoskeleton.browser.state import FILE_ORIGIN, BrowserState
from waggle.clock import SystemClock
from waggle.messages.capping import ElementTarget

pytestmark = pytest.mark.integration  # Every test here starts a real browser.

_TIMEOUTS = PlaywrightTimeouts(navigation_s=10.0, element_s=2.0)
_SETTLE_POLLS = 200  # Reads of the URL a login may take to land (each a round trip).


@dataclass
class _Site:
    """The fixture site served over http, and every path it was asked for."""

    port: int
    requested: list[str] = field(default_factory=list)

    def url(self, host: str, page: str) -> str:
        return f"http://{host}:{self.port}/{page}.html"

    def origin(self, host: str) -> str:
        return f"http://{host}:{self.port}"


class _Capture:
    """A CdpConnector keeping the connection, so a test can reach the page Playwright drives."""

    def __init__(self) -> None:
        self.connection: CdpConnection | None = None

    async def __call__(self, endpoint: str) -> Browser:
        self.connection = await attach_cdp(endpoint, _TIMEOUTS)
        return PlaywrightBrowser(self.connection, SystemClock(), _TIMEOUTS)


@pytest.fixture
def served() -> Iterator[_Site]:
    """Serve the fixture site on loopback for one test, logging each request's path."""
    site = _Site(port=0)

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            site.requested.append(self.path)

    handler = functools.partial(_Handler, directory=str(SITE_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    site.port = int(server.server_address[1])
    # The standard library's server blocks, so it runs on a thread of its own (codingrules 2).
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield site
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
async def real() -> AsyncIterator[tuple[OpenBrowser, CdpConnection]]:
    """A real Chromium, and the Playwright connection driving it; skipped where there is none."""
    capture = _Capture()
    harness = RealBrowserHarness(connector=capture)
    reason = harness.missing()
    if reason is not None:
        pytest.skip(reason)
    opened = await harness.open()
    try:
        assert capture.connection is not None
        yield opened, capture.connection
    finally:
        await harness.close()


def _storage(connection: CdpConnection) -> CheckpointStorage:
    calls = PlaywrightCalls(_TIMEOUTS.limit_s)
    return CheckpointStorage(
        connection.page, connection.context, calls, _TIMEOUTS.navigation_s * 1e3
    )


async def _log_in(browser: Browser, login_url: str) -> None:
    """Log in on the login page at `login_url` and wait until the welcome page is showing."""
    await browser.navigate(login_url)
    await browser.fill(ElementTarget(label="Username"), LOGIN_USERNAME)
    await browser.fill(ElementTarget(label="Password"), LOGIN_PASSWORD)
    await browser.click(ElementTarget(role="button", name="Log in"))
    for _ in range(_SETTLE_POLLS):
        if (await browser.url()).endswith("welcome.html"):
            return
        await browser.title()  # One more round trip to the browser before reading again.
    raise AssertionError("the login never reached the welcome page")


async def test_capture_and_replace_cover_cookies_and_storage_over_http(
    real: tuple[OpenBrowser, CdpConnection], served: _Site
) -> None:
    opened, connection = real
    await opened.browser.navigate(served.url("127.0.0.1", "login"))
    storage = _storage(connection)
    before = await storage.capture()

    await _log_in(opened.browser, served.url("127.0.0.1", "login"))
    after = await storage.capture()
    await storage.replace(before)
    restored = await storage.capture()

    origin = served.origin("127.0.0.1")
    assert before.cookies == ()
    assert [cookie.name for cookie in after.cookies] == [SESSION_KEY]
    assert after.local_storage[origin] == {SESSION_KEY: LOGIN_USERNAME}
    assert restored.cookies == ()
    assert restored.local_storage[origin] == {}


async def test_an_origin_the_page_left_is_read_where_its_server_never_sees(
    real: tuple[OpenBrowser, CdpConnection], served: _Site
) -> None:
    opened, connection = real
    storage = _storage(connection)
    await _log_in(opened.browser, served.url("localhost", "login"))
    await opened.browser.navigate(served.url("127.0.0.1", "long"))

    state = await storage.capture()

    assert state.local_storage[served.origin("localhost")] == {SESSION_KEY: LOGIN_USERNAME}
    # The blank tab asked for the origin's root; it was answered in the browser, not served.
    assert "/" not in served.requested
    assert await opened.browser.url() == served.url("127.0.0.1", "long")  # The page never moved.
    assert len(connection.context.pages) == 1  # The blank tab is closed again.


async def test_file_storage_is_reached_through_a_directory_listing(
    real: tuple[OpenBrowser, CdpConnection], served: _Site
) -> None:
    opened, connection = real
    storage = _storage(connection)
    await _log_in(opened.browser, (SITE_DIR / "login.html").as_uri())
    await opened.browser.navigate(served.url("127.0.0.1", "long"))

    held = await storage.capture()
    await storage.replace(BrowserState())
    cleared = await storage.capture()

    assert held.local_storage[FILE_ORIGIN] == {SESSION_KEY: LOGIN_USERNAME}
    assert cleared.local_storage[FILE_ORIGIN] == {}
