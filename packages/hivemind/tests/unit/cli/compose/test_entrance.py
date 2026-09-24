"""Test hivemind.cli.compose.entrance: `hive serve` refuses an unsafe exposure before listening.

ADR-0033: every remote mode needs TLS on a DNS name, lan and tunnel also mutual TLS, tunnel a
command to run; a mode whose prerequisites do not hold refuses to start, naming the rule, before
any socket is bound; loopback serves on its own; a loopback listener that cannot bind refuses too.
Each test composes the Hive `hive serve` runs (the fake provider, the Hive's own SQLite file) and
enters ``serve_hive`` on this host, with a fake interface table where a mode reads interfaces.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import asyncio
import socket
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from builders.cli import fake_manifest

from hivemind.cli.compose.entrance import ServedHive, build_served_hive, serve_hive
from hivemind.entrance.app import OPENAPI_PATH
from hivemind.entrance.expose import ExposureRefusedError, ExposureRule, FakeInterfaces
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock

_OVERLAY = "100.101.102.103"  # Inside [entrance] vpn_cidrs' default, on the Tailscale interface.
_NAMED = 'public_url = "https://hive.example.ts.net"\n'  # Every remote mode's DNS name.


def _served(tmp_path: Path, entrance: str) -> ServedHive:
    """Compose `hive serve`'s Hive over a fake manifest with ``entrance`` as its section."""
    path = fake_manifest(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n[entrance]\n{entrance}", encoding="utf-8"
    )
    served = build_served_hive(load_manifest(path, {}), environ={}, clock=SystemClock())
    return replace(
        served, interfaces=FakeInterfaces({"tailscale0": [_OVERLAY], "lo": ["127.0.0.1"]})
    )


async def _enter(served: ServedHive) -> int:
    """Enter serve_hive, fetch the contract on loopback, and return its status."""
    async with serve_hive(served) as entrance:
        url = f"http://localhost:{entrance.listeners.loopback_port}{OPENAPI_PATH}"
        async with httpx.AsyncClient() as http:
            return (await http.get(url)).status_code


def _refusal(served: ServedHive) -> ExposureRule:
    """Enter serve_hive expecting a refusal; return the rule it named."""
    with pytest.raises(ExposureRefusedError) as caught:
        asyncio.run(_enter(served))
    return caught.value.rule


def test_loopback_serves_the_contract(tmp_path: Path) -> None:
    served = _served(tmp_path, 'bind = "127.0.0.1:0"\n')

    status = asyncio.run(_enter(served))

    assert status == 200


def test_lan_without_mutual_tls_refuses(tmp_path: Path) -> None:
    served = _served(
        tmp_path,
        'bind = "127.0.0.1:0"\nexpose = "lan"\nremote_bind = "192.168.1.20:8711"\n'
        f"mutual_tls = false\n{_NAMED}",
    )

    assert _refusal(served) is ExposureRule.MUTUAL_TLS_REQUIRED


def test_vpn_without_tls_refuses(tmp_path: Path) -> None:
    served = _served(
        tmp_path, f'bind = "127.0.0.1:0"\nexpose = "vpn"\nremote_bind = "{_OVERLAY}:8711"\n{_NAMED}'
    )

    assert _refusal(served) is ExposureRule.TLS_NOT_CONFIGURED


def test_tunnel_without_a_command_refuses(tmp_path: Path) -> None:
    served = _served(
        tmp_path,
        f'bind = "127.0.0.1:0"\nexpose = "tunnel"\nremote_bind = "127.0.0.1:8711"\n{_NAMED}',
    )

    assert _refusal(served) is ExposureRule.TUNNEL_COMMAND_MISSING


def test_a_loopback_listener_that_cannot_bind_refuses_to_start(tmp_path: Path) -> None:
    # Hold the port for the whole test, so the Entrance's own bind can only fail.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        served = _served(tmp_path, f'bind = "127.0.0.1:{port}"\n')

        with pytest.raises(OSError):
            asyncio.run(_enter(served))
