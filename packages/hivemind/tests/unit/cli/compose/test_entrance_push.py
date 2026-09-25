"""Test `hive serve` push client: over the network, unless a test injects a transport.

Push deliveries (every webhook and Web Push request) leave through httpx's own network transport,
TLS verified for the pinned name and with no environment proxy, unless a test injects a transport
through ``build_served_hive(push_transport=...)``, which then carries every one of them: the seam
the end-to-end push tests record through, with the destination guard still vetting each one.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
from builders.cli import fake_manifest
from unit.entrance.push.support import HOOK_URL, Recorder

from hivemind.cli.compose import entrance as entrance_module
from hivemind.cli.compose.entrance import ServedHive, build_served_hive, serve_hive
from hivemind.entrance.runtime import BuiltEntrance, EntranceParts, build_entrance
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

_LOOPBACK = 'bind = "127.0.0.1:0"\n'  # Pushing needs no remote listener.


def _served(tmp_path: Path, push: httpx.AsyncBaseTransport | None = None) -> ServedHive:
    """Compose `hive serve`'s Hive over a fake manifest, loopback only, with ``push`` injected."""
    path = fake_manifest(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n[entrance]\n{_LOOPBACK}", encoding="utf-8"
    )
    manifest = load_manifest(path, {})
    return build_served_hive(manifest, environ={}, clock=SystemClock(), push_transport=push)


def _handed_push_client(monkeypatch: pytest.MonkeyPatch) -> list[httpx.AsyncClient]:
    """Record the push client `serve_hive` hands the Entrance, which is still built for real."""
    handed: list[httpx.AsyncClient] = []

    def spy(parts: EntranceParts, loopback: socket.socket) -> BuiltEntrance:
        """Keep the parts' HTTP client, then build exactly what `serve_hive` asked for."""
        handed.append(parts.http)
        return build_entrance(parts, loopback)

    monkeypatch.setattr(entrance_module, "build_entrance", spy)
    return handed


async def _serve_and_post(served: ServedHive, handed: list[httpx.AsyncClient]) -> None:
    """Serve, and send one request through the push client the Entrance was handed (if asked)."""
    async with serve_hive(served):
        if served.push_transport is not None:
            # Latency: an in-process transport answers at once; nothing reaches a network.
            await handed[0].post(HOOK_URL, content=b"{}")


def test_push_deliveries_go_over_the_network_when_no_transport_is_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handed = _handed_push_client(monkeypatch)
    served = _served(tmp_path)

    asyncio.run(_serve_and_post(served, handed))

    assert served.push_transport is None
    [client] = handed
    # httpx's own transport: the network, TLS verified against the system trust store.
    assert isinstance(client._transport, httpx.AsyncHTTPTransport)
    assert client.trust_env is False  # No environment proxy between a push and its pinned address.


def test_an_injected_push_transport_carries_every_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handed, recorder = _handed_push_client(monkeypatch), Recorder()
    served = _served(tmp_path, push=httpx.MockTransport(recorder))

    asyncio.run(_serve_and_post(served, handed))

    assert [str(request.url) for request in recorder.requests] == [HOOK_URL]
