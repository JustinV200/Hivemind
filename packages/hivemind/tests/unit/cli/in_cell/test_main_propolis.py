"""Tests for hivemind.cli.in_cell.main as a Propolis Cell: it announces its tier and its own checks.

A Cell whose bootstrap names PROPOLIS dials the Queen's ordinary endpoint (no onion, no proxy) and
announces PROPOLIS on a `CellReady` that carries the tunnel checks it makes of its own routes and
resolvers; the wire model refuses a tiered `CellReady` with no checks, which is why a Cell used to
refuse the tier outright. The Cell reads its own /proc, so each test swaps in the files of a Cell
with a tunnel or without one. A Cell without one still announces, its checks failed and its
heartbeat saying the shield is not verified: the announcement is a report, and the Hive's
attestation of the image from outside is what decides readiness.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/main.py (codingrules 5.1: split by feature from test_main.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/cli/in_cell/test_main_night_veil.py for the Night Veil Cell this is modelled on.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.swarm import linux_route, linux_route6, resolv_conf

from hivemind.cell import CombShieldLevel
from hivemind.cell.tunnel import CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_EGRESS, CHECK_IPV6
from hivemind.cli.in_cell.main import run_in_cell_warden
from hivemind.wardens.deps import WardenDeps
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import new_hive_id, new_node_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.messages.labels import Urgency
from waggle.signing import Ed25519Signer
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
_CLOCK = SystemClock()
_CELL_ID = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_HEADER = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT"
_FOUR = [CHECK_DEFAULT_ROUTE, CHECK_DNS, CHECK_IPV6, CHECK_EGRESS]


def _own_files(rows: list[tuple[str, str]], nameserver: str) -> dict[str, str]:
    """The text of this Cell's own /proc/net/route, ipv6_route and resolv.conf, by file name."""
    table = "\n".join([_HEADER, *(linux_route(i, n) for i, n in rows)]) + "\n"
    return {
        "route": table,
        "ipv6_route": linux_route6("tun0", "::/0") + "\n",
        "resolv.conf": resolv_conf(nameserver),
    }


def _show(monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> None:
    """Make the Cell's own files read as `files`, by name."""
    monkeypatch.setattr(
        "hivemind.cli.in_cell.propolis.read_text", lambda path: files.get(path.name, "")
    )


async def _announce(
    tmp_path: Path, extra: dict[str, str] | None = None
) -> tuple[CellReady, CellHeartbeat, list[WardenDeps]]:
    """Run a PROPOLIS Cell against a loopback Queen; return its CellReady, heartbeat and deps."""
    queen_signer = Ed25519Signer.generate()
    server = WebSocketServer(Codec(signer=queen_signer))
    await server.start()
    built: list[WardenDeps] = []
    hive_id, queen_node_id = new_hive_id(_CLOCK), new_node_id(_CLOCK)
    environ = {
        "HIVEMIND_QUEEN_WAGGLE_URL": server.uri,
        "HIVEMIND_COMB_SHIELD": "PROPOLIS",
        "HIVEMIND_CELL_ID": _CELL_ID,
        "HIVEMIND_HIVE_ID": hive_id,
        "HIVEMIND_QUEEN_NODE_ID": queen_node_id,
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": queen_signer.public_key_bytes.hex(),
        "HIVEMIND_SCRATCH_ROOT": str(tmp_path),
        **(extra or {}),
    }
    run_task = asyncio.ensure_future(
        run_in_cell_warden(environ, _CLOCK, on_deps_built=built.append)
    )
    try:
        accepted = await asyncio.wait_for(anext(server.connections()), timeout=WAIT_S)
        frames = accepted.receive()
        ready = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
        await asyncio.wait_for(anext(frames), timeout=WAIT_S)  # The CapacityReport.
        beat = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
        assert isinstance(ready, CellReady) and isinstance(beat, CellHeartbeat)
        hop = Hop(sender=hive_id, recipient=ready.warden_id, node_id=queen_node_id)
        stop = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
        await accepted.send(wrap(stop, hop, clock=_CLOCK))
        await asyncio.wait_for(run_task, timeout=WAIT_S)
    finally:
        run_task.cancel()
        await server.close()
    return ready, beat, built


async def test_a_propolis_cell_announces_its_tier_and_the_tunnel_checks_it_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    full = [("tun0", "0.0.0.0/1"), ("tun0", "128.0.0.0/1"), ("eth0", "0.0.0.0/0")]
    _show(monkeypatch, _own_files(full, "10.8.0.1"))

    ready, beat, built = await _announce(tmp_path)

    assert ready.comb_shield is WireCombShieldLevel.PROPOLIS
    assert {check.name: check.has_passed for check in ready.attestation} == dict.fromkeys(
        _FOUR, True
    )
    assert beat.is_shield_verified
    [deps] = built
    [cell] = await deps.source.cells()
    assert cell.comb_shield is CombShieldLevel.PROPOLIS  # The Warden's floors see the tier too.


async def test_a_propolis_cell_with_no_tunnel_still_announces_with_its_checks_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _show(monkeypatch, _own_files([("eth0", "0.0.0.0/0")], "8.8.8.8"))

    ready, beat, _built = await _announce(tmp_path)

    # A report and not a gate: the Cell says what it sees, and the Hive's own attestation decides.
    assert ready.comb_shield is WireCombShieldLevel.PROPOLIS
    passed = {check.name: check.has_passed for check in ready.attestation}
    assert passed[CHECK_DEFAULT_ROUTE] is False and passed[CHECK_EGRESS] is False
    assert not beat.is_shield_verified
