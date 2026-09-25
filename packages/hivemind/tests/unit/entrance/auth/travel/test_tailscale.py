"""Tests for hivemind.entrance.auth.travel.tailscale: where tailscaled says a peer connects from.

tailscaled's local API is replaced by ``httpx.MockTransport`` serving canned status documents in
the shape ``GET /localapi/v0/status`` returns (the fields the source reads, and a few it ignores).

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/travel/tailscale.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.travel.tailscale for the module under test.
"""

from __future__ import annotations

import json
import socket
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from hivemind.entrance.auth.travel import (
    LOCAL_API_BASE_URL,
    STATUS_PATH,
    TailscaleEndpointSource,
    tailscale_source,
    unix_socket_client,
)
from hivemind.entrance.errors import TravelLockUnavailableError
from hivemind.manifest import EntranceSection

_STATUS = {
    "Version": "1.76.1",
    "BackendState": "Running",
    "Peer": {
        "nodekey:aa": {
            "HostName": "phone",
            "TailscaleIPs": ["100.64.0.7", "fd7a:115c:a1e0::7"],
            "CurAddr": "203.0.113.54:41641",
            "Relay": "nyc",
            "Online": True,
        },
        "nodekey:bb": {"TailscaleIPs": ["100.64.0.8"], "CurAddr": "", "Relay": "fra"},
        "nodekey:cc": {"TailscaleIPs": ["100.64.0.9"], "CurAddr": "[2001:db8:1:2::5]:41641"},
        "nodekey:dd": {"TailscaleIPs": ["100.64.0.10"], "CurAddr": "", "Relay": ""},
    },
}


def _source(handler: Callable[[httpx.Request], httpx.Response]) -> TailscaleEndpointSource:
    """A source whose client talks to ``handler`` instead of tailscaled."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=LOCAL_API_BASE_URL)
    return TailscaleEndpointSource(client)


def _serving(status: object) -> Callable[[httpx.Request], httpx.Response]:
    """A handler answering the status path with ``status`` and anything else with a 404."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != STATUS_PATH or request.url.host != "local-tailscaled.sock":
            return httpx.Response(404)
        return httpx.Response(200, content=json.dumps(status).encode())

    return handle


def _unreachable(request: httpx.Request) -> httpx.Response:
    """A handler for a tailscaled that is not there."""
    raise httpx.ConnectError("no tailscaled", request=request)


@pytest.mark.parametrize(
    ("address", "network"),
    [
        ("100.64.0.7", "203.0.113.0/24"),  # Direct: its endpoint's /24.
        ("fd7a:115c:a1e0::7", "203.0.113.0/24"),  # The same peer by its IPv6 overlay address.
        ("::ffff:100.64.0.7", "203.0.113.0/24"),  # Reported through a dual-stack socket.
        ("100.64.0.8", "derp:fra"),  # Relayed: its DERP region.
        ("100.64.0.9", "2001:db8:1:2::/64"),  # Direct over IPv6: the endpoint's /64.
        ("100.64.0.10", None),  # Neither an endpoint nor a relay.
        ("100.64.0.99", None),  # Not a peer at all.
        ("not-an-address", None),
    ],
)
async def test_current_network_reads_the_peers_endpoint_or_its_relay(
    address: str, network: str | None
) -> None:
    source = _source(_serving(_STATUS))

    assert await source.current_network(address) == network
    await source.aclose()


@pytest.mark.parametrize(
    "handler",
    [
        _serving({"Peer": None}),
        _serving({"Peer": {"nodekey:aa": {"TailscaleIPs": "100.64.0.7"}}}),  # A malformed peer.
        lambda request: httpx.Response(500),
        lambda request: httpx.Response(200, content=b"not json"),
        _unreachable,
    ],
)
async def test_any_failure_answers_unknown_rather_than_raising(
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    source = _source(handler)

    assert await source.current_network("100.64.0.7") is None
    await source.aclose()


def test_tailscale_source_refuses_where_it_cannot_see_endpoints(tmp_path: Path) -> None:
    vpn = EntranceSection(expose="vpn", travel_lock=True)
    regular = tmp_path / "not-a-socket"
    regular.write_text("")

    refusals: list[tuple[EntranceSection, Path, str]] = [
        (EntranceSection(travel_lock=True), tmp_path / "x.sock", "linux"),
        (vpn, tmp_path / "x.sock", "win32"),
        (vpn, tmp_path / "missing.sock", "linux"),
        (vpn, regular, "linux"),
    ]

    reasons = []
    for section, path, platform in refusals:
        with pytest.raises(TravelLockUnavailableError) as error:
            tailscale_source(section, path, platform)
        reasons.append(error.value.reason)
    assert "not 'vpn'" in reasons[0]
    assert "named pipe" in reasons[1]
    assert all("no tailscaled socket" in reason for reason in reasons[2:])


async def test_tailscale_source_builds_over_a_real_unix_socket(tmp_path: Path) -> None:
    if sys.platform == "win32":
        # tailscaled listens on a named pipe there, and Python has no AF_UNIX on Windows.
        pytest.skip("tailscaled serves a named pipe on Windows, never a Unix socket")
    else:
        path = tmp_path / "tailscaled.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listening:
            listening.bind(str(path))

            source = tailscale_source(EntranceSection(expose="vpn", travel_lock=True), path)

            assert isinstance(source, TailscaleEndpointSource)
            await source.aclose()


async def test_the_real_client_targets_the_local_api_over_the_socket(tmp_path: Path) -> None:
    client = unix_socket_client(tmp_path / "tailscaled.sock")

    assert str(client.base_url).rstrip("/") == LOCAL_API_BASE_URL
    await client.aclose()
