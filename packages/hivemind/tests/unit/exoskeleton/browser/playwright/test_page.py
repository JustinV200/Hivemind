"""Unit tests for hivemind.exoskeleton.browser.playwright.page: PlaywrightBrowser specifics.

The Browser clauses themselves are proven over this backend and the fake by the contract suite;
this module covers what only the Playwright backend has: keeping password values out of its aria
snapshots however YAML quotes them, and a browser whose process is stopped under it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

pytest.importorskip("playwright.async_api")

from contracts.browser_harness import OpenBrowser, RealBrowserHarness

from hivemind.exoskeleton.browser.excerpts import REDACTED
from hivemind.exoskeleton.browser.playwright import redact_field_values
from hivemind.exoskeleton.errors import PeripheralError
from waggle.messages.capping import ElementTarget


@pytest.fixture
async def real() -> AsyncIterator[tuple[RealBrowserHarness, OpenBrowser]]:
    """A real Chromium on the fixture site, skipped where there is none."""
    harness = RealBrowserHarness()
    reason = harness.missing()
    if reason is not None:
        pytest.skip(reason)
    opened = await harness.open()
    try:
        yield harness, opened
    finally:
        await harness.close()


def test_redaction_blanks_a_plain_value_at_the_end_of_its_line() -> None:
    snapshot = '- textbox "Username": alice\n- textbox "Password": honeycomb\n- button "Log in"'

    redacted = redact_field_values(snapshot, ["honeycomb"])

    assert redacted == (
        f'- textbox "Username": alice\n- textbox "Password": {REDACTED}\n- button "Log in"'
    )


def test_redaction_blanks_a_value_yaml_had_to_quote() -> None:
    snapshot = '- textbox "Password": "a: b\\"c"'

    assert redact_field_values(snapshot, ['a: b"c']) == f'- textbox "Password": {REDACTED}'


def test_redaction_matches_the_value_playwright_collapsed() -> None:
    snapshot = '- textbox "Password": two spaces'

    assert redact_field_values(snapshot, ["  two  spaces  "]) == f'- textbox "Password": {REDACTED}'


def test_redaction_cuts_a_long_value_wherever_it_appears() -> None:
    snapshot = '- paragraph: my honeycomb notes\n- textbox "Password": x'

    redacted = redact_field_values(snapshot, ["honeycomb"])

    assert redacted == f'- paragraph: my {REDACTED} notes\n- textbox "Password": x'


def test_redaction_cuts_a_short_value_only_where_a_field_prints_it() -> None:
    snapshot = '- paragraph: a bc d\n- textbox "Password": bc'

    redacted = redact_field_values(snapshot, ["bc", ""])

    assert redacted == f'- paragraph: a bc d\n- textbox "Password": {REDACTED}'


@pytest.mark.integration
async def test_a_password_yaml_quotes_never_reaches_a_snapshot(
    real: tuple[RealBrowserHarness, OpenBrowser],
) -> None:
    _, opened = real
    await opened.browser.navigate(opened.url("login"))

    await opened.browser.fill(ElementTarget(label="Password"), ' tricky: "quoted" ')
    snapshot = await opened.browser.snapshot()

    assert "tricky" not in snapshot
    assert REDACTED in snapshot


@pytest.mark.integration
async def test_a_browser_stopped_under_it_fails_every_call_and_closes_quietly(
    real: tuple[RealBrowserHarness, OpenBrowser],
) -> None:
    harness, opened = real
    await opened.browser.navigate(opened.url("login"))

    await harness.stop_launched()  # What detach does, before or without a close.

    with pytest.raises(PeripheralError):
        await opened.browser.title()
    await opened.browser.close()  # Does not raise.
    await opened.browser.close()
