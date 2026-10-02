"""Unit tests for hivemind.exoskeleton.browser.playwright.hosts: no request leaves its hosts.

`HostGuard.decide` is driven with a stand-in for Playwright's Route (the one object it touches);
the route itself is installed on a real Chromium by the contract harness, which the real-browser
suites exercise where a browser is installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import Error as PlaywrightError

from hivemind.exoskeleton.browser.hosts import BlockRule, BrowserBlock, HostScopes
from hivemind.exoskeleton.browser.playwright.guard import BLOCKED
from hivemind.exoskeleton.browser.playwright.hosts import HostGuard, is_web_request
from hivemind.guard import CapabilitySet


@dataclass
class _Request:
    url: str


@dataclass
class _Route:
    """Records how the guard answered; raises like a closed page when `gone`."""

    request: _Request
    gone: bool = False
    answers: list[str] = field(default_factory=list)

    async def continue_(self) -> None:
        self._answer("continue")

    async def abort(self, error_code: str) -> None:
        self._answer(f"abort:{error_code}")

    def _answer(self, answer: str) -> None:
        if self.gone:
            raise PlaywrightError("Target page, context or browser has been closed")
        self.answers.append(answer)


@dataclass
class _Sink:
    blocks: list[BrowserBlock] = field(default_factory=list)

    async def __call__(self, block: BrowserBlock) -> None:
        self.blocks.append(block)


def _guard(sink: _Sink, *, private_loopback: bool = False) -> HostGuard:
    hosts = HostScopes(CapabilitySet.parse("net:api.example.com"), private_loopback)
    return HostGuard(hosts, sink)


@pytest.mark.parametrize(
    ("url", "web"),
    [
        ("https://a.example/", True),
        ("HTTP://a.example/", True),
        ("file:///x", False),
        ("data:text/plain,x", False),
        ("blob:https://a.example/1", False),
        ("about:blank", False),
    ],
)
def test_the_route_answers_every_web_request_and_nothing_else(url: str, web: bool) -> None:
    assert is_web_request(url) is web


async def test_a_request_to_a_granted_host_continues_and_reports_nothing() -> None:
    sink = _Sink()
    route = _Route(_Request("https://api.example.com/v1/items?q=1"))

    await _guard(sink).decide(route)  # type: ignore[arg-type]

    assert route.answers == ["continue"]
    assert sink.blocks == []


async def test_a_request_a_page_makes_to_another_host_is_blocked_and_reported_by_host() -> None:
    sink = _Sink()
    route = _Route(_Request("https://collector.example.org/beacon?secret=abc"))

    await _guard(sink).decide(route)  # type: ignore[arg-type]

    assert route.answers == [f"abort:{BLOCKED}"]
    # The host is reported, never the URL's path or query.
    assert sink.blocks == [BrowserBlock(BlockRule.NET, "collector.example.org")]


async def test_loopback_is_blocked_on_a_shared_loopback_and_allowed_on_a_private_one() -> None:
    sink = _Sink()
    shared, private = _Route(_Request("http://127.0.0.1:8080/")), _Route(_Request("http://[::1]/"))

    await _guard(sink).decide(shared)  # type: ignore[arg-type]
    await _guard(sink, private_loopback=True).decide(private)  # type: ignore[arg-type]

    assert shared.answers == [f"abort:{BLOCKED}"]
    assert private.answers == ["continue"]
    assert sink.blocks == [BrowserBlock(BlockRule.NET, "127.0.0.1")]


async def test_a_request_whose_host_cannot_be_read_is_blocked() -> None:
    sink = _Sink()
    route = _Route(_Request("http://[::1/"))

    await _guard(sink).decide(route)  # type: ignore[arg-type]

    assert route.answers == [f"abort:{BLOCKED}"]
    assert sink.blocks == [BrowserBlock(BlockRule.NET, None)]


async def test_a_page_that_closed_while_deciding_is_neither_answered_nor_reported() -> None:
    sink = _Sink()
    route = _Route(_Request("https://elsewhere.example/"), gone=True)

    await _guard(sink).decide(route)  # type: ignore[arg-type]

    assert route.answers == []
    assert sink.blocks == []


async def test_without_a_sink_a_block_still_holds() -> None:
    route = _Route(_Request("https://elsewhere.example/"))

    await HostGuard(HostScopes()).decide(route)  # type: ignore[arg-type]

    assert route.answers == [f"abort:{BLOCKED}"]
