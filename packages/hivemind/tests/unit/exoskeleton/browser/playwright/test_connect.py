"""Unit tests for hivemind.exoskeleton.browser.playwright.connect: attaching Playwright over CDP."""

from __future__ import annotations

import socket

import pytest

pytest.importorskip("playwright.async_api")

from hivemind.exoskeleton.browser.playwright.calls import CALL_MARGIN_S
from hivemind.exoskeleton.browser.playwright.connect import (
    PlaywrightTimeouts,
    attach_cdp,
)
from hivemind.exoskeleton.errors import AttachError


def _closed_port() -> int:
    """Return a loopback port nothing listens on (bound, read, released)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_the_asyncio_bound_is_the_longest_timeout_plus_the_margin() -> None:
    timeouts = PlaywrightTimeouts(navigation_s=30.0, element_s=5.0)

    assert timeouts.limit_s == 30.0 + CALL_MARGIN_S


@pytest.mark.integration
async def test_attaching_where_no_browser_listens_is_an_attach_error() -> None:
    # Playwright's driver really starts beside the test; only the browser is missing.
    timeouts = PlaywrightTimeouts(navigation_s=5.0, element_s=1.0, connect_s=5.0)

    with pytest.raises(AttachError, match="could not attach to the browser over CDP"):
        await attach_cdp(f"http://127.0.0.1:{_closed_port()}", timeouts)
