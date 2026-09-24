"""Tests for hivemind.cli.in_cell.main as a Night Veil Cell: its link goes only through Tor.

Roadmap step 10.3a's exit bullet, run for real: a Cell whose bootstrap names NIGHT_VEIL, the
Queen's onion service and the Tor SOCKS proxy on its own loopback dials the Queen only through that
proxy (here a `FakeSocksProxy` playing Tor, routing the onion name to a real loopback
`WebSocketServer` standing in for the Hive Stand), announces NIGHT_VEIL with its control-link
checks passed, and builds a Warden whose floors see NIGHT_VEIL and refuse `net` to the Hive
Stand's onion by name. A proxy that refuses leaves the Cell unconnected: it never dials directly.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/main.py (codingrules 5.1: split by feature from
    test_main.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.socks for the proxy exchange and FakeSocksProxy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from hivemind.cell import CombShieldLevel
from hivemind.cli.in_cell.link import HIDDEN_SERVICE_CHECK, VIA_SOCKS_CHECK
from hivemind.cli.in_cell.main import run_in_cell_warden
from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    EnforcementPoint,
    PolicyContext,
    PolicyRequest,
    floor_decision,
    worker_principal,
)
from hivemind.wardens.deps import WardenDeps
from waggle.clock import FakeClock, SystemClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.errors import ConnectFailedError
from waggle.ids import HiveId, NodeId, new_hive_id, new_node_id, new_worker_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.messages.labels import Urgency
from waggle.messages.task import WorkerRole
from waggle.signing import Ed25519Signer
from waggle.transport.socks import FakeSocksBehaviour, FakeSocksProxy
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
_CLOCK = SystemClock()
_CELL_ID = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"
_ONION = "7jjm54ntxrtbp4fjhhw2gdk7zz2fshgnubimtmc5dcczncvdfo3lnbid.onion"  # A valid v3 address.
_ONION_PORT = 8710  # The onion service's virtual port; the fake proxy maps it to the listener.


@dataclass
class _Stand:
    """The Hive Stand's side: its signed listener, its identity, and the fake Tor in front."""

    server: WebSocketServer
    proxy: FakeSocksProxy
    signer: Ed25519Signer
    hive_id: HiveId
    node_id: NodeId


@pytest.fixture
async def stand() -> AsyncIterator[_Stand]:
    """A signed loopback listener, and a fake Tor SOCKS port routing the onion name to it."""
    signer = Ed25519Signer.generate()
    server = WebSocketServer(Codec(signer=signer))
    await server.start()
    proxy = FakeSocksProxy({_ONION: ("127.0.0.1", server.port)})
    await proxy.start()
    try:
        yield _Stand(server, proxy, signer, new_hive_id(_CLOCK), new_node_id(_CLOCK))
    finally:
        await proxy.close()
        await server.close()


def _environ(stand: _Stand, scratch_root: Path) -> dict[str, str]:
    """The environment a Night Veil Cell's bootstrap renders (`CellBootstrap.environment()`)."""
    return {
        "HIVEMIND_QUEEN_WAGGLE_URL": f"ws://{_ONION}:{_ONION_PORT}",
        "HIVEMIND_SOCKS_PROXY_URL": stand.proxy.url(),
        "HIVEMIND_COMB_SHIELD": "NIGHT_VEIL",
        "HIVEMIND_CELL_ID": _CELL_ID,
        "HIVEMIND_HIVE_ID": stand.hive_id,
        "HIVEMIND_QUEEN_NODE_ID": stand.node_id,
        "HIVEMIND_CELL_SIGNING_KEY": Ed25519Signer.generate().private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": stand.signer.public_key_bytes.hex(),
        "HIVEMIND_SCRATCH_ROOT": str(scratch_root),
    }


class _HurriedClock(FakeClock):
    """A FakeClock whose every wait passes at once: backoff runs its course with no real sleep."""

    async def sleep(self, seconds: float) -> None:
        """Advance past `seconds` and return, yielding to the loop once."""
        self.advance(seconds)
        await asyncio.sleep(0)


def _refused_net(deps: WardenDeps, host: str) -> str | None:
    """The floor rule that refuses one of this Cell's Workers `net:<host>`, or None."""
    request = PolicyRequest(
        principal=worker_principal(new_worker_id(_CLOCK), WorkerRole.DRONE),
        point=EnforcementPoint.TOOL_INVOCATION,
        needed=Capability(family=CapabilityFamily.NET, scope=host),
        held=CapabilitySet.parse("net:*"),
        context=PolicyContext(comb_shield=CombShieldLevel.NIGHT_VEIL),
    )
    decision = floor_decision(request, deps.guard)
    return decision.rule if decision is not None else None


async def test_a_night_veil_cell_reaches_the_queen_only_through_tor_and_announces_its_tier(
    stand: _Stand, tmp_path: Path
) -> None:
    built: list[WardenDeps] = []
    run_task = asyncio.ensure_future(
        run_in_cell_warden(_environ(stand, tmp_path), _CLOCK, on_deps_built=built.append)
    )
    try:
        connections = stand.server.connections()
        accepted = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
        frames = accepted.receive()
        ready = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
        await asyncio.wait_for(anext(frames), timeout=WAIT_S)  # The CapacityReport.
        beat = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
        assert isinstance(ready, CellReady)
        hop = Hop(sender=stand.hive_id, recipient=ready.warden_id, node_id=stand.node_id)
        stop = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
        await accepted.send(wrap(stop, hop, clock=_CLOCK))
        await asyncio.wait_for(run_task, timeout=WAIT_S)
    finally:
        run_task.cancel()

    # The one route to the Hive Stand was the proxy, asked for the onion name, never resolved.
    assert stand.proxy.requests == [(_ONION, _ONION_PORT)]
    assert isinstance(ready, CellReady)
    assert ready.comb_shield is WireCombShieldLevel.NIGHT_VEIL
    assert {check.name: check.has_passed for check in ready.attestation} == {
        VIA_SOCKS_CHECK: True,
        HIDDEN_SERVICE_CHECK: True,
    }
    assert isinstance(beat, CellHeartbeat)
    assert beat.is_shield_verified
    # The Warden this Cell built sees its tier, and never resolved the onion for its floors.
    [deps] = built
    [cell] = await deps.source.cells()
    assert cell.comb_shield is CombShieldLevel.NIGHT_VEIL
    assert deps.guard.hive_state.own_addresses == frozenset()
    assert _refused_net(deps, _ONION) == "guard.state_floor.loopback"


async def test_a_night_veil_cell_whose_proxy_refuses_never_dials_directly(
    stand: _Stand, tmp_path: Path
) -> None:
    refusing = FakeSocksProxy(behaviour=FakeSocksBehaviour(refuse_with=0xF0))
    await refusing.start()
    environ = {**_environ(stand, tmp_path), "HIVEMIND_SOCKS_PROXY_URL": refusing.url()}
    try:
        with pytest.raises(ConnectFailedError, match="through the SOCKS proxy"):
            await asyncio.wait_for(run_in_cell_warden(environ, _HurriedClock()), timeout=WAIT_S)
    finally:
        await refusing.close()

    assert refusing.requests  # It asked the proxy, and only the proxy.
    assert stand.proxy.requests == []  # The working route was never tried behind its back.
