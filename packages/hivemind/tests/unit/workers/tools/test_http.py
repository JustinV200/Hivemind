"""Unit tests for hivemind.workers.tools.http: http_request, authorised by the Guard and the gate.

Roadmap step 10.3: the destination's `net:<host>` is checked through the Guard's `Enforcer` at the
`tool_invocation` point -- a refusal is a readable result for the model and a `guard.denied` row on
the trail -- and a well-formed call with `net` held passes the Capping gate on its `network_egress`
tier, after which `_send` makes the one request, pinned to the address the Guard checked (roadmap
step 10.3a; the loopback floor itself is tested in `test_http_floors.py`). Every test that could
reach `_send` replaces it (or `httpx`'s own transport) first, and every context resolves through a
`FakeResolver`, so nothing here opens a socket or performs a lookup.
"""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock

import httpx
import pytest
from builders.cells import make_cell
from builders.workers import make_assignment, make_context

from hivemind.cell import AccessLevel, CellKind
from hivemind.guard import CapabilitySet
from hivemind.guard.net import DEFAULT_ANSWER
from hivemind.pheromone import TrailQuery
from hivemind.workers.tools import http as http_module
from hivemind.workers.tools.http import PinnedRequest, http_request
from hivemind.workers.tools.registry import ToolInvocation


def _invocation_with_net_capability(host: str = "example.com") -> ToolInvocation:
    """A Worker holding `net:<host>` on a FULL Cell (a SCRATCH Cell never permits `net`)."""
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.FULL)
    ctx = make_context(cell=cell, capabilities=CapabilitySet.parse(f"net:{host}"))
    return ToolInvocation(ctx=ctx, assignment=make_assignment())


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace `_send` with a sentinel answering `200: ok`, so no test here reaches a socket."""
    sentinel = AsyncMock(return_value="200: ok")
    monkeypatch.setattr(http_module, "_send", sentinel)
    return sentinel


async def test_http_request_without_a_net_capability_is_refused_with_the_reason_on_the_trail(
    sent: AsyncMock,
) -> None:
    # A FULL Cell, so the refusal is the missing capability, not the Cell's access level.
    ctx = make_context(cell=make_cell(kind=CellKind.REAL, access_level=AccessLevel.FULL))
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await http_request(invocation, {"method": "GET", "url": "https://example.com/x"})

    assert result.startswith("refused by the Guard (guard.not_held)")
    sent.assert_not_called()
    [denial] = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "tool_invocation"
    assert denial.payload["capability"] == "net:example.com"
    assert denial.subject_id == ctx.worker_id


async def test_http_request_for_a_host_its_net_scope_does_not_cover_is_refused(
    sent: AsyncMock,
) -> None:
    invocation = _invocation_with_net_capability("example.com")

    result = await http_request(invocation, {"method": "GET", "url": "https://other.org/x"})

    assert result.startswith("refused by the Guard")
    sent.assert_not_called()


async def test_http_request_on_a_scratch_cell_is_refused_even_with_net_held(
    sent: AsyncMock,
) -> None:
    ctx = make_context(capabilities=CapabilitySet.parse("net:example.com"))  # A SCRATCH Cell.
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await http_request(invocation, {"method": "GET", "url": "https://example.com/x"})

    assert "guard.access_level.scratch" in result
    sent.assert_not_called()


async def test_http_request_with_net_held_passes_the_gate_and_sends_once(sent: AsyncMock) -> None:
    invocation = _invocation_with_net_capability()
    url = "https://example.com/resource"

    result = await http_request(invocation, {"method": "POST", "url": url, "body": "x=1"})

    assert result == "200: ok"
    sent.assert_awaited_once()
    assert sent.await_args is not None
    method, pinned, body = sent.await_args.args
    assert (method, str(pinned.url), body) == ("POST", url, "x=1")
    # Pinned to the address the fake resolver answered, the one the Guard's floors checked.
    assert str(pinned.address) == DEFAULT_ANSWER[0]
    kinds = [event.kind for event in await invocation.ctx.trail.query(TrailQuery())]
    assert "capping.verified" in kinds
    assert "guard.denied" not in kinds


@pytest.mark.parametrize("url", ["https:///no-host", "https://exa mple.com/x", "not a url"])
async def test_http_request_with_no_valid_host_is_refused_without_raising(url: str) -> None:
    # Roadmap step 10.1: a net scope is a host or an address; a model's malformed URL gets a
    # readable refusal instead of an exception out of the tool.
    invocation = _invocation_with_net_capability()

    result = await http_request(invocation, {"method": "GET", "url": url})

    assert "names no valid host" in result


async def test_http_request_rejects_an_unsupported_method() -> None:
    invocation = _invocation_with_net_capability()

    result = await http_request(invocation, {"method": "DELETE", "url": "https://example.com"})

    assert "method must be one of" in result


def _client_answering(handler: httpx.MockTransport) -> type[httpx.AsyncClient]:
    """An `httpx.AsyncClient` subclass whose every request goes to `handler`, never a socket."""

    class _Client(httpx.AsyncClient):
        def __init__(self, **kwargs: object) -> None:
            super().__init__(transport=handler, timeout=1.0)

    return _Client


def _pinned(url: str = "https://example.com/", address: str = "203.0.113.10") -> PinnedRequest:
    """A request for `url` pinned to `address`, as `http_request` builds one after the floors."""
    parsed = httpx.URL(url)
    return PinnedRequest(
        url=parsed, host=parsed.host.lower(), address=ipaddress.ip_address(address)
    )


async def test_send_returns_the_status_and_text(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="hello"))
    monkeypatch.setattr(httpx, "AsyncClient", _client_answering(transport))

    assert await http_module._send("GET", _pinned(), None) == "200: hello"


async def test_send_reports_a_failed_request_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(httpx, "AsyncClient", _client_answering(httpx.MockTransport(refuse)))

    result = await http_module._send("GET", _pinned(), None)

    assert "failed: ConnectError" in result


async def test_send_connects_to_the_checked_address_with_the_name_kept_for_the_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WHY: a second lookup inside the client could be answered differently (DNS rebinding).
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204)

    monkeypatch.setattr(httpx, "AsyncClient", _client_answering(httpx.MockTransport(record)))

    await http_module._send("GET", _pinned("https://api.example.com:8443/v1?q=1"), None)

    [request] = seen
    assert str(request.url) == "https://203.0.113.10:8443/v1?q=1"
    assert request.headers["host"] == "api.example.com:8443"
    assert request.headers["connection"] == "close"
    assert request.extensions["sni_hostname"] == "api.example.com"


def test_a_plain_http_pin_sends_no_sni_and_brackets_an_ipv6_address() -> None:
    pinned = _pinned("http://api.example.com/x", "2001:db8::7")

    assert pinned.extensions() == {}
    assert str(pinned.request_url()) == "http://[2001:db8::7]/x"
    assert pinned.headers()["Host"] == "api.example.com"
