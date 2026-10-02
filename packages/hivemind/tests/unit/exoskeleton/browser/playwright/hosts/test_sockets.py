"""Unit tests for hivemind.exoskeleton.browser.playwright.hosts.sockets: sockets stay put."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("playwright.async_api")

from playwright.async_api import Error as PlaywrightError

from hivemind.exoskeleton.browser.hosts import BlockRule, BrowserBlock, HostScopes
from hivemind.exoskeleton.browser.playwright.hosts.sockets import (
    POLICY_VIOLATION,
    SocketGuard,
    is_socket_url,
)
from hivemind.guard import CapabilitySet

_GRANTED = HostScopes(CapabilitySet.parse("net:chat.example.com"))


@dataclass
class _Socket:
    """Records how the guard answered a socket; raises like a closed page when `gone`."""

    url: str
    gone: bool = False
    answers: list[str] = field(default_factory=list)

    def connect_to_server(self) -> None:
        self.answers.append("connect")

    async def close(self, code: int, reason: str) -> None:
        if self.gone:
            raise PlaywrightError("Target page, context or browser has been closed")
        self.answers.append(f"close:{code}")


@dataclass
class _Sink:
    blocks: list[BrowserBlock] = field(default_factory=list)

    async def __call__(self, block: BrowserBlock) -> None:
        self.blocks.append(block)


async def _decide(socket: _Socket, sink: _Sink | None = None) -> None:
    await SocketGuard(_GRANTED, sink).decide(socket)  # type: ignore[arg-type, unused-ignore]


@pytest.mark.parametrize(
    ("url", "routed"),
    [("ws://a.example/", True), ("WSS://a.example/", True), ("https://a.example/", False)],
)
def test_the_socket_route_answers_ws_and_wss_only(url: str, routed: bool) -> None:
    assert is_socket_url(url) is routed


async def test_a_socket_to_a_granted_host_is_connected_and_not_reported() -> None:
    sink, socket = _Sink(), _Socket("wss://chat.example.com/live")

    await _decide(socket, sink)

    assert socket.answers == ["connect"]
    assert sink.blocks == []


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("wss://evil.example/x?secret=1", "evil.example"),
        ("ws://127.0.0.1:9000/", "127.0.0.1"),  # Loopback needs `net:loopback` here too.
        ("ws://[::1/", None),  # No host can be read: refused.
    ],
)
async def test_a_socket_to_any_other_host_is_closed_and_reported_by_host(
    url: str, host: str | None
) -> None:
    sink, socket = _Sink(), _Socket(url)

    await _decide(socket, sink)

    assert socket.answers == [f"close:{POLICY_VIOLATION}"]
    assert sink.blocks == [BrowserBlock(BlockRule.NET, host)]


async def test_a_socket_whose_page_closed_is_neither_answered_nor_reported() -> None:
    sink, socket = _Sink(), _Socket("wss://evil.example/", gone=True)

    await _decide(socket, sink)

    assert socket.answers == []
    assert sink.blocks == []


async def test_without_a_sink_a_socket_is_still_closed() -> None:
    socket = _Socket("wss://evil.example/")

    await _decide(socket)

    assert socket.answers == [f"close:{POLICY_VIOLATION}"]
