"""Unit tests for hivemind.exoskeleton.browser.fake.browser: FakeBrowser beyond the contract suite.

The contract suite proves the clauses FakeBrowser shares with the real browser; these cover the
fake's own machinery: cookies and where a page may set one, guards that loop, what a snapshot line
looks like, the frames it draws, and every operation refused once it is closed.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from hivemind.exoskeleton.browser import BrowserCheckpoint
from hivemind.exoskeleton.browser.fake import (
    FIXTURE_ORIGIN,
    LOGIN_PASSWORD,
    LOGIN_USERNAME,
    MAX_GUARD_HOPS,
    SESSION_KEY,
    FakeBrowser,
    FakeElement,
    FakePage,
    FakeSite,
    SetCookie,
    SetStorage,
    login_site,
)
from hivemind.exoskeleton.browser.state import BrowserState
from hivemind.exoskeleton.errors import ElementNotFoundError, PeripheralError
from waggle.clock import FakeClock
from waggle.messages.capping import ElementTarget

_LOGIN = f"{FIXTURE_ORIGIN}/login"
_WELCOME = f"{FIXTURE_ORIGIN}/welcome"


def _browser(site: FakeSite | None = None) -> FakeBrowser:
    return FakeBrowser(site or login_site(), FakeClock())


async def _log_in(browser: FakeBrowser) -> None:
    await browser.navigate(_LOGIN)
    await browser.fill(ElementTarget(label="Username"), LOGIN_USERNAME)
    await browser.fill(ElementTarget(label="Password"), LOGIN_PASSWORD)
    await browser.click(ElementTarget(role="button", name="Log in"))


def _state(checkpoint: BrowserCheckpoint) -> BrowserState:
    return BrowserState.load(checkpoint.state)


async def test_a_login_sets_the_session_item_and_a_cookie_that_restore_removes() -> None:
    browser = _browser()
    await browser.navigate(_LOGIN)
    before = await browser.checkpoint()

    await _log_in(browser)
    during = _state(await browser.checkpoint())
    await browser.restore(before)
    after = _state(await browser.checkpoint())

    assert [(c.domain, c.name, c.secure) for c in during.cookies] == [
        ("fixture.test", SESSION_KEY, True)
    ]
    assert during.local_storage == {FIXTURE_ORIGIN: {SESSION_KEY: LOGIN_USERNAME}}
    assert after.cookies == ()
    assert after.local_storage == {}
    assert await browser.url() == _LOGIN


async def test_a_file_page_takes_no_cookie_and_about_blank_no_storage() -> None:
    click = (SetCookie("c", "1"), SetStorage("k", "v"))
    page = FakePage(
        url="file:///site/a.html",
        title="A",
        elements=(FakeElement("go", role="button", name="Go", on_click=click),),
    )
    browser = _browser(FakeSite(pages=(page,)))
    await browser.navigate(page.url)

    await browser.click(ElementTarget(role="button", name="Go"))

    state = _state(await browser.checkpoint())
    assert state.cookies == ()
    assert state.local_storage == {"file://": {"k": "v"}}


async def test_guards_that_send_a_visitor_round_in_a_loop_are_an_error() -> None:
    first = FakePage(url="https://a.test/1", title="1", requires="k", otherwise="https://a.test/2")
    second = FakePage(url="https://a.test/2", title="2", requires="k", otherwise="https://a.test/1")
    browser = _browser(FakeSite(pages=(first, second)))

    with pytest.raises(PeripheralError, match="redirect in a loop"):
        await browser.navigate(first.url)
    assert MAX_GUARD_HOPS >= 2


async def test_a_missing_page_is_an_error_naming_no_url_and_the_page_stays() -> None:
    browser = _browser()
    await browser.navigate(_LOGIN)

    with pytest.raises(PeripheralError) as raised:
        await browser.navigate(f"{FIXTURE_ORIGIN}/nowhere?token=abc")

    assert "token" not in str(raised.value)
    assert await browser.url() == _LOGIN


async def test_press_records_the_browser_spelling_and_only_enter_does_anything() -> None:
    browser = _browser()
    await browser.navigate(_LOGIN)

    await browser.press("ctrl+a", ElementTarget(label="Username"))
    await browser.press("Tab")

    assert browser.pressed == ["Control+a", "Tab"]
    assert await browser.url() == _LOGIN


async def test_filling_something_that_is_not_a_field_is_refused() -> None:
    browser = _browser()
    await browser.navigate(_LOGIN)

    with pytest.raises(PeripheralError, match="not a text field") as raised:
        await browser.fill(ElementTarget(role="button", name="Log in"), "x")

    assert not isinstance(raised.value, ElementNotFoundError)


async def test_a_snapshot_reads_like_an_aria_snapshot() -> None:
    browser = _browser()
    await browser.navigate(_LOGIN)
    await browser.fill(ElementTarget(label="Username"), "bob")
    await browser.fill(ElementTarget(label="Password"), "wrong")
    await browser.click(ElementTarget(role="button", name="Log in"))

    lines = (await browser.snapshot()).splitlines()

    assert '- heading "Log in"' in lines
    assert "- text: Username" in lines
    assert '- textbox "Username": bob' in lines
    assert '- textbox "Password": [redacted]' in lines
    assert "- alert: Wrong username or password" in lines


async def test_frames_are_real_pngs_in_a_colour_of_each_pages_own() -> None:
    browser = _browser()
    await browser.navigate(_LOGIN)
    login_first = await browser.screenshot()
    login_again = await browser.screenshot()
    await browser.navigate(f"{FIXTURE_ORIGIN}/long")

    other = await browser.screenshot()

    assert login_first.sha256 == login_again.sha256
    assert other.sha256 != login_first.sha256
    assert (other.width, other.height) == (320, 200)


def _operations(browser: FakeBrowser) -> list[Callable[[], Awaitable[object]]]:
    target = ElementTarget(label="Username")
    checkpoint = BrowserCheckpoint(url="about:blank", state=BrowserState().dump())
    return [
        lambda: browser.navigate(_LOGIN),
        lambda: browser.click(target),
        lambda: browser.fill(target, "x"),
        lambda: browser.press("Enter"),
        browser.url,
        browser.title,
        browser.snapshot,
        lambda: browser.element_text(target),
        browser.page_text,
        browser.screenshot,
        browser.checkpoint,
        lambda: browser.restore(checkpoint),
    ]


async def test_every_operation_is_refused_once_closed() -> None:
    browser = _browser()
    await browser.close()

    for operation in _operations(browser):
        with pytest.raises(PeripheralError, match="connection is closed"):
            await operation()


async def test_the_welcome_page_greets_only_a_logged_in_visitor() -> None:
    browser = _browser()
    await browser.navigate(_WELCOME)
    assert await browser.url() == _LOGIN  # No session: sent back to log in.

    await _log_in(browser)

    assert await browser.url() == _WELCOME
    assert await browser.element_text(ElementTarget(selector="#greeting")) == "Welcome, alice"
