"""Unit tests for hivemind.exoskeleton.browser.playwright.calls: bounded calls, mapped errors."""

from __future__ import annotations

import asyncio

import pytest

playwright_api = pytest.importorskip("playwright.async_api")

from hivemind.exoskeleton.browser.playwright.calls import (  # noqa: E402 -- after the skip.
    WITHHELD,
    PlaywrightCalls,
    close_quietly,
    reason,
)
from hivemind.exoskeleton.errors import ElementNotFoundError, PeripheralError  # noqa: E402
from waggle.messages.capping import ElementTarget  # noqa: E402

PlaywrightError = playwright_api.Error
PlaywrightTimeoutError = playwright_api.TimeoutError


async def _answer() -> str:
    return "Log in - HiveMind fixture"


async def test_run_returns_what_the_call_returns() -> None:
    assert await PlaywrightCalls(limit_s=5.0).run("read the title", _answer) == (
        "Log in - HiveMind fixture"
    )


async def test_after_close_a_call_is_refused_without_being_made() -> None:
    calls = PlaywrightCalls(limit_s=5.0)
    made: list[str] = []

    async def call() -> None:
        made.append("made")

    assert calls.close() is True
    assert calls.close() is False
    with pytest.raises(PeripheralError, match="connection is closed"):
        await calls.run("click", call)
    assert made == []


async def test_a_playwright_timeout_is_a_peripheral_error_or_a_missing_element() -> None:
    calls = PlaywrightCalls(limit_s=5.0)
    target = ElementTarget(role="button", name="Sign up")

    async def times_out() -> None:
        raise PlaywrightTimeoutError("Locator.wait_for: Timeout 2000ms exceeded.")

    with pytest.raises(PeripheralError, match="did not respond in time") as plain:
        await calls.run("screenshot", times_out)
    with pytest.raises(ElementNotFoundError) as missing:
        await calls.run("click", times_out, missing=target)

    assert not isinstance(plain.value, ElementNotFoundError)
    assert missing.value.target == target.describe()
    assert missing.value.operation == "click"


async def test_a_playwright_error_keeps_only_its_first_line_without_the_api_name() -> None:
    async def fails() -> None:
        raise PlaywrightError(
            "Locator.click: Error: strict mode violation: resolved to 2 elements:\n"
            "    1) <button>Log in</button>\nCall log:\n  - waiting for get_by_text"
        )

    with pytest.raises(PeripheralError) as raised:
        await PlaywrightCalls(limit_s=5.0).run("click", fails)

    assert raised.value.reason == "strict mode violation: resolved to 2 elements:"


async def test_an_error_quoting_the_typed_text_is_withheld() -> None:
    async def fails() -> None:
        raise PlaywrightError('Locator.fill: Error: could not fill "hunter2-secret"')

    with pytest.raises(PeripheralError) as raised:
        await PlaywrightCalls(limit_s=5.0).run("fill", fails, typed="hunter2-secret")

    assert raised.value.reason == WITHHELD
    assert "hunter2" not in str(raised.value)


async def test_a_call_outliving_its_bound_is_a_peripheral_error() -> None:
    never = asyncio.Event()

    with pytest.raises(PeripheralError, match="did not answer within"):
        await PlaywrightCalls(limit_s=0.01).run("read the title", never.wait)


def test_reason_masks_urls_and_falls_back_to_the_class_name() -> None:
    failed = PlaywrightError("Page.goto: net::ERR_FILE_NOT_FOUND at file:///home/x/login.html")

    assert reason(failed) == "net::ERR_FILE_NOT_FOUND at <url>"
    assert reason(PlaywrightError("")) == "Error"


async def test_close_quietly_logs_a_failed_close_instead_of_raising() -> None:
    class _Gone:
        async def close(self) -> None:
            raise PlaywrightError("Target page, context or browser has been closed")

    await close_quietly(_Gone())  # Does not raise.
