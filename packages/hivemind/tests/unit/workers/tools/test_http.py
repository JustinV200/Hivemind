"""Unit tests for hivemind.workers.tools.http: http_request, always refused by v0's Capping gate.

Codingrules section 8.6's "prompted rung, not the strongest one" is not the concern here; the
concern is that v0 never opens a socket. `test_send_is_never_called` proves it by monkeypatching
`hivemind.workers.tools.http._send` to a sentinel and asserting it is never invoked, rather than by
mocking a network transport -- `_send` is the one function that would import `httpx` at all.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from builders.workers import make_assignment, make_context

from hivemind.guard import CapabilitySet
from hivemind.workers.tools import http as http_module
from hivemind.workers.tools.http import http_request
from hivemind.workers.tools.registry import ToolInvocation


def _invocation_with_net_capability(host: str = "example.com") -> ToolInvocation:
    ctx = make_context(capabilities=CapabilitySet.parse(f"net:{host}"))
    return ToolInvocation(ctx=ctx, assignment=make_assignment())


async def test_http_request_without_a_net_capability_is_refused_before_proposing() -> None:
    ctx = make_context()  # No `net:*` capability at all.
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())

    result = await http_request(invocation, {"method": "GET", "url": "https://example.com/x"})

    assert "no net capability covers" in result


async def test_http_request_with_a_net_capability_is_rejected_by_v0s_gate() -> None:
    invocation = _invocation_with_net_capability()

    result = await http_request(
        invocation, {"method": "GET", "url": "https://example.com/resource"}
    )

    assert "state=REJECTED" in result
    assert "network egress is not cappable in v0" in result


async def test_http_request_rejects_an_unsupported_method() -> None:
    invocation = _invocation_with_net_capability()

    result = await http_request(invocation, {"method": "DELETE", "url": "https://example.com"})

    assert "method must be one of" in result


async def test_send_is_never_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves nothing is sent: v0's gate can never reach the state that would call `_send`."""
    sentinel = AsyncMock(return_value="unreachable")
    monkeypatch.setattr(http_module, "_send", sentinel)
    invocation = _invocation_with_net_capability()

    await http_request(invocation, {"method": "POST", "url": "https://example.com/submit"})

    sentinel.assert_not_called()
