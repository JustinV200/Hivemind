"""Unit tests for hivemind.exoskeleton.browser.playwright.hosts.requests: requests stay put.

`HostGuard.decide` is driven with stand-ins for Playwright's Route, Request and APIResponse (the
objects it touches); the routes themselves were checked on a real Edge (ADR-0059), and the
contract harness installs them on a real Chromium where one is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import Error as PlaywrightError

from hivemind.exoskeleton.browser.hosts import BlockRule, BrowserBlock, HostScopes, page_policy
from hivemind.exoskeleton.browser.playwright.guard import BLOCKED
from hivemind.exoskeleton.browser.playwright.hosts.answers import POLICY_HEADER
from hivemind.exoskeleton.browser.playwright.hosts.peers import PEER_CONNECTIONS_OFF
from hivemind.exoskeleton.browser.playwright.hosts.requests import (
    FAILED,
    HostGuard,
    is_web_request,
)
from hivemind.guard import CapabilitySet

_GRANTED = HostScopes(CapabilitySet.parse("net:api.example.com", "net:cdn.example.com"))


@dataclass
class _Request:
    url: str
    resource_type: str = "image"
    method: str = "GET"
    navigation: bool = False

    def is_navigation_request(self) -> bool:
        return self.navigation


@dataclass
class _Response:
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class _Route:
    """Records how the guard answered; raises like a closed page when `gone`."""

    request: _Request
    response: _Response | None = None  # What a fetch returns; None makes the fetch fail.
    gone: bool = False
    answers: list[str] = field(default_factory=list)
    fulfilled: dict[str, object] = field(default_factory=dict)
    fetched: dict[str, object] = field(default_factory=dict)

    async def continue_(self) -> None:
        self._answer("continue")

    async def abort(self, error_code: str) -> None:
        self._answer(f"abort:{error_code}")

    async def fetch(self, **options: object) -> _Response:
        self.fetched.update(options)
        if self.response is None:
            raise PlaywrightError("net::ERR_CONNECTION_REFUSED")
        return self.response

    async def fulfill(self, **options: object) -> None:
        self._answer("fulfill")
        self.fulfilled.update(options)

    def _answer(self, answer: str) -> None:
        if self.gone:
            raise PlaywrightError("Target page, context or browser has been closed")
        self.answers.append(answer)


@dataclass
class _Sink:
    blocks: list[BrowserBlock] = field(default_factory=list)

    async def __call__(self, block: BrowserBlock) -> None:
        self.blocks.append(block)


@dataclass
class _Context:
    """Records what the guard installs on the browser context."""

    installed: list[str] = field(default_factory=list)

    async def route(self, url: object, handler: object) -> None:
        self.installed.append("route")

    async def route_web_socket(self, url: object, handler: object) -> None:
        self.installed.append("route_web_socket")

    async def add_init_script(self, script: str) -> None:
        self.installed.append(script)


def _document(url: str, response: _Response | None, method: str = "GET") -> _Route:
    return _Route(_Request(url, "document", method, navigation=True), response)


async def _decide(route: _Route, sink: _Sink, hosts: HostScopes = _GRANTED) -> None:
    await HostGuard(hosts, sink).decide(route)  # type: ignore[arg-type, unused-ignore]


@pytest.mark.parametrize(
    ("url", "web"),
    [
        ("https://a.example/", True),
        ("HTTP://a.example/", True),
        ("ws://a.example/", False),
        ("file:///x", False),
        ("data:text/plain,x", False),
        ("blob:https://a.example/1", False),
        ("about:blank", False),
    ],
)
def test_the_route_answers_every_web_request_and_nothing_else(url: str, web: bool) -> None:
    assert is_web_request(url) is web


async def test_install_routes_requests_and_sockets_and_turns_webrtc_off() -> None:
    context = _Context()

    await HostGuard(_GRANTED).install(context)  # type: ignore[arg-type, unused-ignore]

    assert context.installed == ["route", "route_web_socket", PEER_CONNECTIONS_OFF]


async def test_a_subresource_to_a_granted_host_continues_in_the_browser_unfetched() -> None:
    sink = _Sink()
    route = _Route(_Request("https://api.example.com/v1/items?q=1", "fetch"), _Response())

    await _decide(route, sink)

    assert route.answers == ["continue"]
    assert route.fetched == {}  # It streams in the browser; the page policy holds its redirects.
    assert sink.blocks == []


async def test_a_request_a_page_makes_to_another_host_is_blocked_and_reported_by_host() -> None:
    sink = _Sink()
    route = _Route(_Request("https://collector.example.org/beacon?secret=abc"))

    await _decide(route, sink)

    assert route.answers == [f"abort:{BLOCKED}"]
    # The host is reported, never the URL's path or query.
    assert sink.blocks == [BrowserBlock(BlockRule.NET, "collector.example.org")]


async def test_loopback_is_blocked_on_a_shared_loopback_and_allowed_on_a_private_one() -> None:
    sink = _Sink()
    shared, private = _Route(_Request("http://127.0.0.1:8080/")), _Route(_Request("http://[::1]/"))

    await _decide(shared, sink)
    await _decide(private, sink, HostScopes(_GRANTED.granted, private_loopback=True))

    assert shared.answers == [f"abort:{BLOCKED}"]
    assert private.answers == ["continue"]
    assert sink.blocks == [BrowserBlock(BlockRule.NET, "127.0.0.1")]


async def test_a_request_whose_host_cannot_be_read_is_blocked() -> None:
    sink = _Sink()
    route = _Route(_Request("http://[::1/"))

    await _decide(route, sink)

    assert route.answers == [f"abort:{BLOCKED}"]
    assert sink.blocks == [BrowserBlock(BlockRule.NET, None)]


async def test_a_page_that_closed_while_deciding_is_neither_answered_nor_reported() -> None:
    sink = _Sink()
    route = _Route(_Request("https://elsewhere.example/"), gone=True)

    await _decide(route, sink)

    assert route.answers == []
    assert sink.blocks == []


async def test_without_a_sink_a_block_still_holds() -> None:
    route = _Route(_Request("https://elsewhere.example/"))

    await HostGuard(HostScopes()).decide(route)  # type: ignore[arg-type, unused-ignore]

    assert route.answers == [f"abort:{BLOCKED}"]


async def test_a_page_is_fetched_one_hop_and_answered_with_the_lease_policy_beside_its_own() -> (
    None
):
    sink = _Sink()
    site = {"content-type": "text/html", POLICY_HEADER: "img-src 'none'"}
    route = _document("https://api.example.com/", _Response(200, site))

    await _decide(route, sink)

    assert route.fetched["max_redirects"] == 0
    assert route.answers == ["fulfill"]
    headers = route.fulfilled["headers"]
    assert isinstance(headers, dict)
    # Both policies are enforced: the site's own is kept, never replaced.
    assert headers[POLICY_HEADER] == f"img-src 'none', {page_policy(_GRANTED)}"
    assert headers["content-type"] == "text/html"
    assert sink.blocks == []


async def test_a_lease_allowed_every_host_adds_no_policy() -> None:
    hosts = HostScopes(CapabilitySet.parse("net:*"))
    route = _document("https://anywhere.example/", _Response(200, {"content-type": "text/html"}))

    await _decide(route, _Sink(), hosts)

    assert route.fulfilled["headers"] == {"content-type": "text/html"}


async def test_a_redirect_to_a_refused_host_is_blocked_before_the_browser_follows_it() -> None:
    sink = _Sink()
    leave = _Response(302, {"location": "https://evil.example/collect?d=1"})
    route = _document("https://api.example.com/out", leave)

    await _decide(route, sink)

    assert route.answers == [f"abort:{BLOCKED}"]
    assert sink.blocks == [BrowserBlock(BlockRule.NET, "evil.example")]


async def test_a_navigations_allowed_redirect_comes_back_through_the_route_as_a_new_page() -> None:
    sink = _Sink()
    route = _document("https://api.example.com/login", _Response(302, {"location": "/home"}))

    await _decide(route, sink)

    assert route.answers == ["fulfill"]
    assert route.fulfilled["status"] == 200
    body = route.fulfilled["body"]
    assert isinstance(body, str)
    assert 'location.replace("https://api.example.com/home")' in body
    assert sink.blocks == []


async def test_a_scripts_allowed_redirect_is_handed_on_for_the_page_policy_to_hold() -> None:
    redirect = _Response(301, {"location": "https://cdn.example.com/lib.v2.js"})
    route = _Route(_Request("https://api.example.com/lib.js", "script"), redirect)

    await _decide(route, _Sink())

    assert route.answers == ["fulfill"]
    assert route.fulfilled["response"] is redirect


async def test_a_redirect_that_keeps_a_posts_body_is_refused_not_turned_into_a_get() -> None:
    sink = _Sink()
    route = _document("https://api.example.com/form", _Response(307, {"location": "/a"}), "POST")

    await _decide(route, sink)

    assert route.answers == [f"abort:{FAILED}"]
    assert sink.blocks == []  # No host was refused: the hop is allowed, the replay is not.


async def test_a_get_that_is_redirected_with_its_method_kept_is_re_entered() -> None:
    route = _document("https://api.example.com/a", _Response(308, {"location": "/b"}))

    await _decide(route, _Sink())

    assert route.answers == ["fulfill"]
    assert route.fulfilled["status"] == 200


async def test_a_fetch_that_fails_is_aborted_as_a_network_failure_and_not_reported() -> None:
    sink = _Sink()
    route = _document("https://api.example.com/", None)

    await _decide(route, sink)

    assert route.answers == [f"abort:{FAILED}"]
    assert sink.blocks == []
