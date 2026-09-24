"""Unit tests for hivemind.workers.tools.http's hardening: no request ever reaches the Hive Stand.

Roadmap step 10.3a (ADR-0033): a capability string can spell a loopback host many ways the grammar
never sees through, so the HTTP tool checks the name, resolves it, and has the Guard's floors judge
every address it got back; a refusal is `guard.denied` under `guard.state_floor.loopback` and
nothing is sent. The name is checked first, so a host the Worker does not hold is never looked up.

Fits into the Hive:
    Mirrors src/hivemind/workers/tools/http.py (codingrules section 5.1: one module's tests split
    by feature under test).

Key invariants:
    - None: this module holds tests only. Every context resolves through a `FakeResolver`, and
      `_send` is replaced, so nothing here performs a lookup or opens a socket.

See Also:
    - hivemind.workers.tools.http for the module under test.
    - hivemind.guard.policy.floors.hive_state for the floor that refuses.
"""

from __future__ import annotations

import dataclasses
import ipaddress
from unittest.mock import AsyncMock

import pytest
from builders.cells import make_cell
from builders.workers import make_assignment, make_context

from hivemind.cell import AccessLevel, CellIdentity, CellKind
from hivemind.guard import CapabilitySet, Enforcer, load_guard_policy
from hivemind.guard.net import FakeResolver
from hivemind.guard.policy import HiveState
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools import http as http_module
from hivemind.workers.tools.http import http_request
from hivemind.workers.tools.registry import ToolInvocation

_LOOPBACK_RULE = "guard.state_floor.loopback"


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Replace `_send` with a sentinel answering `200: ok`, so no test here reaches a socket."""
    sentinel = AsyncMock(return_value="200: ok")
    monkeypatch.setattr(http_module, "_send", sentinel)
    return sentinel


def _context(resolver: FakeResolver, *held: str) -> WorkerContext:
    """A Worker on a FULL Cell holding `held` (every host by default), resolving with `resolver`."""
    cell = make_cell(kind=CellKind.REAL, access_level=AccessLevel.FULL)
    capabilities = CapabilitySet.parse(*(held or ("net:*",)))
    return make_context(cell=cell, capabilities=capabilities, resolver=resolver)


async def _get(ctx: WorkerContext, url: str) -> str:
    """Run one GET for `url` through the tool."""
    invocation = ToolInvocation(ctx=ctx, assignment=make_assignment())
    return await http_request(invocation, {"method": "GET", "url": url})


async def _denials(ctx: WorkerContext) -> list[PheromoneEvent]:
    """Every `guard.denied` row on `ctx`'s trail."""
    return list(await ctx.trail.query(TrailQuery(kind="guard.denied")))


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8710/v1/devices",
        "http://entrance.localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://169.254.169.254/latest/meta-data/",
    ],
)
async def test_a_loopback_or_link_local_destination_is_refused_by_name_without_a_lookup(
    sent: AsyncMock, url: str
) -> None:
    resolver = FakeResolver()
    ctx = _context(resolver)

    result = await _get(ctx, url)

    assert result.startswith(f"refused by the Guard ({_LOOPBACK_RULE})")
    sent.assert_not_called()
    assert resolver.lookups == []
    [denial] = await _denials(ctx)
    assert denial.payload["rule"] == _LOOPBACK_RULE


@pytest.mark.parametrize("host", ["127.1", "2130706433", "0x7f.1", "rebind.example"])
async def test_a_name_that_resolves_to_loopback_is_refused_on_its_address(
    sent: AsyncMock, host: str
) -> None:
    # Each is a name to the grammar, so `net:*` covers it; only the resolved address tells.
    ctx = _context(FakeResolver({host: ["127.0.0.1"]}))

    result = await _get(ctx, f"http://{host}/")

    assert result.startswith(f"refused by the Guard ({_LOOPBACK_RULE})")
    assert "it resolves to a loopback address" in result
    sent.assert_not_called()
    [denial] = await _denials(ctx)
    assert denial.payload["capability"] == f"net:{host}"
    assert denial.payload["rule"] == _LOOPBACK_RULE


async def test_one_bad_address_among_good_ones_refuses_the_request(sent: AsyncMock) -> None:
    ctx = _context(FakeResolver({"mixed.example": ["93.184.216.34", "::ffff:169.254.1.1"]}))

    result = await _get(ctx, "https://mixed.example/")

    assert "it resolves to a link-local address" in result
    sent.assert_not_called()


async def test_one_of_the_hive_stands_own_addresses_is_refused(sent: AsyncMock) -> None:
    # A composition root states the Hive Stand's addresses once, on the policy's HiveState.
    state = HiveState.of(own_addresses=(ipaddress.ip_address("192.168.1.20"),))
    policy = dataclasses.replace(load_guard_policy(), hive_state=state)
    base = _context(FakeResolver({"stand.lan": ["192.168.1.20"]}))
    identity = CellIdentity(
        hive_id=base.identity.hive_id, node_id=base.identity.node_id, actor="system"
    )
    ctx = dataclasses.replace(base, enforcer=Enforcer(policy, base.trail, base.clock, identity))

    result = await _get(ctx, "http://stand.lan:8710/")

    assert "it resolves to one of the Hive Stand's own addresses" in result
    sent.assert_not_called()


async def test_a_host_the_worker_does_not_hold_is_never_looked_up(sent: AsyncMock) -> None:
    # A lookup is itself a message to whoever answers for the name: never made for an unheld host.
    resolver = FakeResolver()
    ctx = _context(resolver, "net:example.com")

    result = await _get(ctx, "https://exfiltrate.attacker.example/")

    assert result.startswith("refused by the Guard (guard.not_held)")
    assert resolver.lookups == []
    sent.assert_not_called()


async def test_a_host_that_does_not_resolve_is_never_sent_and_is_no_guard_refusal(
    sent: AsyncMock,
) -> None:
    ctx = _context(FakeResolver(default=None))

    result = await _get(ctx, "https://nowhere.example/")

    assert "did not resolve" in result
    sent.assert_not_called()
    assert await _denials(ctx) == []


async def test_a_percent_encoded_host_is_refused_whole_never_cut_short(sent: AsyncMock) -> None:
    resolver = FakeResolver()
    ctx = _context(resolver)

    result = await _get(ctx, "https://exa%20mple.com/")

    assert "names no valid host" in result
    assert resolver.lookups == []
    sent.assert_not_called()


async def test_a_public_destination_is_resolved_once_on_its_own_port_and_pinned(
    sent: AsyncMock,
) -> None:
    resolver = FakeResolver({"api.example.com": ["93.184.216.34"]})
    ctx = _context(resolver)

    result = await _get(ctx, "https://api.example.com:8443/v1")

    assert result == "200: ok"
    assert resolver.lookups == [("api.example.com", 8443)]
    assert sent.await_args is not None
    _method, pinned, _body = sent.await_args.args
    assert str(pinned.address) == "93.184.216.34"
