"""Contract suite for the browser fast path: Browser and BrowserLauncher, fake and Chromium.

Each test states one clause of `hivemind.exoskeleton.browser.base`'s `Browser` or `BrowserLauncher`
contract and runs it over every implementation in `contracts.browser_harness`: the fake
(`FakeBrowserLauncher` serving `login_site()`) and the real one (`ChromiumLauncher` starting a real
Chromium through a real `LocalProcessSession`, Playwright driving it over CDP, against the static
fixture site as file:// URLs). A page reacts after a click returns, so a clause that expects the
page to change polls for it, as the Capping gate's postconditions do; the fake has always changed
already. The real harness skips, saying why, where Playwright or a Chromium-family browser is
missing, and is marked `integration` because it starts a real browser.

Fits into the Hive:
    Test infrastructure (codingrules section 14.3), not shipped. Exercises
    `hivemind.exoskeleton.browser` (launch, locate, the playwright package and fake).

Key invariants:
    - None: this module holds tests only.

See Also:
    - contracts.browser_harness for the two harnesses.
    - docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md for the fast path.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from contracts.browser_harness import (
    ELEMENT_TIMEOUT_S,
    BrowserHarness,
    FakeBrowserHarness,
    OpenBrowser,
    RealBrowserHarness,
)

from hivemind.exoskeleton.browser import (
    MAX_PAGE_TEXT_CHARS,
    MAX_SNAPSHOT_CHARS,
    BrowserCheckpoint,
)
from hivemind.exoskeleton.browser.fake import (
    HELP_LINK,
    HELP_TEXT,
    LOGIN_HEADING,
    LOGIN_PASSWORD,
    LOGIN_TITLE,
    LOGIN_USERNAME,
    LONG_HEADING,
    WELCOME_HEADING,
    WRONG_CREDENTIALS,
)
from hivemind.exoskeleton.browser.files import OUTSIDE_FILE_ROOTS
from hivemind.exoskeleton.errors import AttachError, ElementNotFoundError, PeripheralError
from hivemind.exoskeleton.frames import PNG_SIGNATURE
from waggle.messages.capping import ElementTarget

_SETTLE_S = 10.0  # How long a page may take to react to a click (a file:// load: milliseconds).
_POLL_S = 0.05  # How often a clause re-reads the page while it waits.
_USERNAME = ElementTarget(label="Username")
_PASSWORD = ElementTarget(role="textbox", name="Password")
_LOG_IN = ElementTarget(role="button", name=LOGIN_HEADING)
_GREETING = ElementTarget(role="heading", name=WELCOME_HEADING)
_ABSENT = ElementTarget(role="button", name="Sign up")  # The fixture site has no such button.


@pytest.fixture(
    params=[
        pytest.param(FakeBrowserHarness, id="fake"),
        pytest.param(RealBrowserHarness, id="real", marks=pytest.mark.integration),
    ]
)
async def site(request: pytest.FixtureRequest) -> AsyncIterator[tuple[BrowserHarness, OpenBrowser]]:
    """A freshly launched browser, and the harness that launched it, closed after the test."""
    harness: BrowserHarness = request.param()
    reason = harness.missing()
    if reason is not None:
        pytest.skip(reason)
    opened = await harness.open()
    try:
        yield harness, opened
    finally:
        await harness.close()


async def _eventually[ValueT](read: Callable[[], Awaitable[ValueT]], expected: object) -> ValueT:
    """Re-read until the page shows `expected` or _SETTLE_S passes; return the last reading."""
    deadline = time.monotonic() + _SETTLE_S
    value = await read()
    while value != expected and time.monotonic() < deadline:
        await asyncio.sleep(_POLL_S)
        value = await read()
    return value


async def _log_in(opened: OpenBrowser, attempt: str = LOGIN_PASSWORD) -> None:
    """Load the login page and fill both fields (`attempt` as the password), leaving it focused."""
    await opened.browser.navigate(opened.url("login"))
    await opened.browser.fill(_USERNAME, LOGIN_USERNAME)
    await opened.browser.fill(_PASSWORD, attempt)


# ── Navigation and reading ───────────────────────────────────────────────────


async def test_a_new_browser_is_on_a_blank_page(site: tuple[BrowserHarness, OpenBrowser]) -> None:
    _, opened = site

    assert await opened.browser.url() == "about:blank"


async def test_navigate_loads_the_page_and_reports_its_url_and_title(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site

    await opened.browser.navigate(opened.url("login"))

    assert await opened.browser.url() == opened.url("login")
    assert await opened.browser.title() == LOGIN_TITLE


async def test_navigating_to_a_missing_page_fails_and_the_next_navigation_works(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site

    with pytest.raises(PeripheralError):
        await opened.browser.navigate(opened.url("missing"))
    await opened.browser.navigate(opened.url("login"))

    assert await opened.browser.title() == LOGIN_TITLE


async def test_a_file_url_outside_the_roots_is_refused_and_the_page_stays(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    # Arrange: a page is showing, and a file sits beside scratch, never inside it.
    _, opened = site
    await opened.browser.navigate(opened.url("login"))
    shown = await opened.browser.url()
    outside = (opened.session.scratch_dir.parent / "not-the-lease" / "secret.txt").as_uri()

    with pytest.raises(PeripheralError) as refused:
        await opened.browser.navigate(outside)

    assert refused.value.reason == OUTSIDE_FILE_ROOTS
    assert "secret" not in str(refused.value)  # The refusal never names the file.
    assert await opened.browser.url() == shown


async def test_page_text_is_the_visible_text_only(site: tuple[BrowserHarness, OpenBrowser]) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))

    text = await opened.browser.page_text()

    assert "Username" in text
    assert HELP_TEXT not in text  # In the page, but hidden.


async def test_long_reads_are_cut_to_their_caps(site: tuple[BrowserHarness, OpenBrowser]) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("long"))

    text = await opened.browser.page_text()
    snapshot = await opened.browser.snapshot()

    assert "The hive hums along." in text
    assert MAX_PAGE_TEXT_CHARS // 2 < len(text) <= MAX_PAGE_TEXT_CHARS
    assert LONG_HEADING in snapshot
    assert len(snapshot) <= MAX_SNAPSHOT_CHARS


async def test_snapshot_names_the_button_and_never_shows_a_password(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await _log_in(opened)

    snapshot = await opened.browser.snapshot()

    assert f'button "{LOGIN_HEADING}"' in snapshot
    assert LOGIN_USERNAME in snapshot  # An ordinary field's value is shown...
    assert LOGIN_PASSWORD not in snapshot  # ...a password field's never is.


async def test_element_text_reads_a_field_but_never_a_password(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await _log_in(opened)

    assert await opened.browser.element_text(_USERNAME) == LOGIN_USERNAME
    assert await opened.browser.element_text(_PASSWORD) == ""


async def test_element_text_of_nothing_is_none_without_waiting(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))
    started = time.monotonic()

    text = await opened.browser.element_text(_ABSENT)

    assert text is None
    assert time.monotonic() - started < ELEMENT_TIMEOUT_S / 2  # "Not there now", no wait.


async def test_screenshot_is_a_png_frame(site: tuple[BrowserHarness, OpenBrowser]) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))

    frame = await opened.browser.screenshot()

    assert frame.png.startswith(PNG_SIGNATURE)
    assert frame.width > 0
    assert frame.height > 0


# ── Acting on elements ───────────────────────────────────────────────────────


async def test_fill_by_label_and_role_then_click_by_role_logs_in(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await _log_in(opened)

    await opened.browser.click(_LOG_IN)

    assert await _eventually(opened.browser.url, opened.url("welcome")) == opened.url("welcome")
    assert await opened.browser.element_text(_GREETING) == WELCOME_HEADING


async def test_click_by_selector_submits_the_form(site: tuple[BrowserHarness, OpenBrowser]) -> None:
    _, opened = site
    await _log_in(opened)

    await opened.browser.click(ElementTarget(selector="#submit"))

    assert await _eventually(opened.browser.url, opened.url("welcome")) == opened.url("welcome")


async def test_return_pressed_on_a_field_submits_the_form(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await _log_in(opened)

    await opened.browser.press("Return", ElementTarget(label="Password"))  # xdotool's name.

    assert await _eventually(opened.browser.url, opened.url("welcome")) == opened.url("welcome")


async def test_enter_pressed_on_the_page_reaches_the_focused_field(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await _log_in(opened)  # Filling the password left it focused.

    await opened.browser.press("Enter")

    assert await _eventually(opened.browser.url, opened.url("welcome")) == opened.url("welcome")


async def test_click_by_text_reveals_hidden_text(site: tuple[BrowserHarness, OpenBrowser]) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))
    help_text = ElementTarget(text=HELP_TEXT)
    assert await opened.browser.element_text(help_text) is None

    await opened.browser.click(ElementTarget(text=HELP_LINK))

    assert await _eventually(lambda: opened.browser.element_text(help_text), HELP_TEXT) == HELP_TEXT


async def test_wrong_credentials_show_an_alert_and_stay(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await _log_in(opened, attempt="not-the-password")
    alert = ElementTarget(role="alert")

    await opened.browser.click(_LOG_IN)

    read_alert = lambda: opened.browser.element_text(alert)  # noqa: E731 -- one short probe.
    assert await _eventually(read_alert, WRONG_CREDENTIALS) == WRONG_CREDENTIALS
    assert await opened.browser.url() == opened.url("login")


async def test_a_missing_element_is_element_not_found(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))

    with pytest.raises(ElementNotFoundError) as raised:
        await opened.browser.click(_ABSENT)

    assert raised.value.target == _ABSENT.describe()


async def test_a_target_naming_several_elements_is_refused_with_the_count(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))

    # The heading and the button both read "Log in"; both fields are inputs.
    with pytest.raises(PeripheralError, match="matches 2 elements") as by_text:
        await opened.browser.click(ElementTarget(text=LOGIN_HEADING))
    with pytest.raises(PeripheralError, match="matches 2 elements") as by_selector:
        await opened.browser.fill(ElementTarget(selector="input"), "x")

    assert not isinstance(by_text.value, ElementNotFoundError)
    assert not isinstance(by_selector.value, ElementNotFoundError)


async def test_a_key_it_does_not_know_is_refused(site: tuple[BrowserHarness, OpenBrowser]) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))

    with pytest.raises(PeripheralError):
        await opened.browser.press("Hyperspace")


# ── Checkpoint and restore ───────────────────────────────────────────────────


async def test_restore_undoes_a_login_made_after_the_checkpoint(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await opened.browser.navigate(opened.url("login"))
    checkpoint = await opened.browser.checkpoint()
    await _log_in(opened)
    await opened.browser.click(_LOG_IN)
    assert await _eventually(opened.browser.url, opened.url("welcome")) == opened.url("welcome")

    await opened.browser.restore(checkpoint)

    assert await opened.browser.url() == opened.url("login")
    # The session item is gone with the restore, so the welcome page sends a visitor back.
    await opened.browser.navigate(opened.url("welcome"))
    assert await _eventually(opened.browser.url, opened.url("login")) == opened.url("login")
    assert await opened.browser.element_text(_GREETING) is None


async def test_restore_puts_back_what_the_checkpoint_held(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    await _log_in(opened)
    await opened.browser.click(_LOG_IN)
    assert await _eventually(opened.browser.url, opened.url("welcome")) == opened.url("welcome")
    checkpoint = await opened.browser.checkpoint()
    await opened.browser.navigate(opened.url("long"))

    await opened.browser.restore(checkpoint)

    assert await opened.browser.url() == opened.url("welcome")
    assert await opened.browser.element_text(_GREETING) == WELCOME_HEADING


async def test_a_checkpoint_it_did_not_take_is_refused(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site
    foreign = BrowserCheckpoint(url=opened.url("login"), state="not a browser state")

    with pytest.raises(PeripheralError):
        await opened.browser.restore(foreign)


# ── Closing and the launcher ─────────────────────────────────────────────────


async def test_close_is_idempotent_and_ends_the_connection(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    _, opened = site

    await opened.browser.close()
    await opened.browser.close()

    with pytest.raises(PeripheralError):
        await opened.browser.title()


async def test_stopping_what_the_launch_started_leaves_nothing_running(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    harness, opened = site
    assert opened.processes  # A launch reports what it started, so detach can stop it.

    await harness.stop_launched()

    assert await harness.leftovers() == ()


async def test_a_launch_that_never_gets_ready_stops_its_browser_and_raises(
    site: tuple[BrowserHarness, OpenBrowser],
) -> None:
    harness, _ = site
    await harness.stop_launched()  # Only the unready launch's processes are left to check.

    with pytest.raises(AttachError):
        await harness.launch_unready()

    assert await harness.leftovers() == ()
