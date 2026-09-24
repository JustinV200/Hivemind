"""Integration test: rehearse the fixture login procedure on a real Chromium (roadmap step 6.7).

`@pytest.mark.integration` (codingrules 14.2). A bee's recording of logging in is exported as a
BrowserProcedure, moved onto a local copy of the fixture site served over http, and replayed on a
real Chromium the browser fast path starts on a real LocalProcessSession (the contract harness's
launch): the right password passes; the wrong one fails the action that should have logged in,
with the password never in the report. Skips where Playwright or a Chromium is missing.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises exoskeleton/rehearsal against the real
    Playwright backend end to end.

Key invariants:
    - None: this module holds tests only.

See Also:
    - packages/hivemind/tests/unit/exoskeleton/rehearsal for the same replay on the fake browser.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest
from builders.rehearsal import login_recording
from contracts.browser_harness import SITE_DIR, OpenBrowser, RealBrowserHarness

from hivemind.exoskeleton.attach import Peripherals
from hivemind.exoskeleton.browser.fake import LOGIN_PASSWORD
from hivemind.exoskeleton.rehearsal import export_procedure, rebase, rehearse
from waggle.clock import SystemClock

pytestmark = pytest.mark.integration

_RECORDED_ON = "https://fixture.test"  # Where the bee worked; the rehearsal never goes there.
_SETTLE_S = 5.0  # A real page's redirect and render, with room for a slow runner.


class _QuietHandler(SimpleHTTPRequestHandler):
    """Serve the fixture site without a line on stderr per request."""

    def log_message(self, format: str, *args: object) -> None:
        """Say nothing: the test reads the report, not the access log."""


@pytest.fixture
def site() -> Iterator[str]:
    """Serve the fixture login site on loopback, on a free port; yield its origin."""
    handler = functools.partial(_QuietHandler, directory=str(SITE_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
async def browser() -> AsyncIterator[OpenBrowser]:
    """A real Chromium on a real session, stopped and cleaned up afterwards."""
    harness = RealBrowserHarness()
    if reason := harness.missing():
        pytest.skip(reason)
    opened = await harness.open()
    try:
        yield opened
    finally:
        await harness.close()


async def test_the_exported_login_passes_its_rehearsal_on_a_real_browser(
    site: str, browser: OpenBrowser
) -> None:
    procedure = rebase(
        export_procedure("fixture-login", "rec_1", login_recording(_RECORDED_ON, ".html")), site
    )

    report = await rehearse(
        procedure,
        Peripherals(browser=browser.browser),
        SystemClock(),
        {"password": LOGIN_PASSWORD},
        _SETTLE_S,
    )

    assert report.passed, report.model_dump_json(indent=2)
    assert report.site == site
    assert LOGIN_PASSWORD not in report.model_dump_json()


async def test_a_wrong_password_fails_the_login_action_on_a_real_browser(
    site: str, browser: OpenBrowser
) -> None:
    procedure = rebase(
        export_procedure("fixture-login", "rec_1", login_recording(_RECORDED_ON, ".html")), site
    )

    report = await rehearse(
        procedure, Peripherals(browser=browser.browser), SystemClock(), {"password": "wrong"}, 1.0
    )

    assert report.passed is False
    login = report.actions[-1]
    assert login.failure is None  # Every step ran: the page simply never welcomed alice.
    assert [pc.has_held for pc in login.postconditions] == [False, False]
