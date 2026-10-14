"""Tests for hivemind.cli.in_cell.main as a Night Veil Cell whose Tor link drops under it.

Roadmap phase 11's exit bullet: "killing the Tor daemon while leaving the VPN and default route up
clusters the Cell instead of falling back to a direct or VPN-only connection". Run for real on
loopback: a Night Veil Cell is connected to a listener standing in for the Hive Stand through a
`FakeSocksProxy` playing Tor, and then Tor dies (the proxy is closed, which drops the relayed
connection too). The Cell must be cut off, then cluster fully past the Hive's offline limit
(`HIVEMIND_MAX_OFFLINE_S`), while nothing at all reaches the listener (the listener is right there
on loopback: a Cell that fell back to any other route could have reached it), and when Tor comes
back on the port it had the same Warden announces itself again over it. A connection that merely
drops, with Tor still up, is redialed through Tor alone.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/main.py (codingrules 5.1: split by feature from
    test_main_night_veil.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/e2e/test_night_veil_tor_loss.py for the same loss through a whole Hive.
    - hivemind.cli.in_cell.redial for the link under test.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.cell_listener import until
from builders.tor_stand import ONION, ONION_PORT, TorStand, tor_stand

from hivemind.cli.in_cell.link import HIDDEN_SERVICE_CHECK, VIA_SOCKS_CHECK
from hivemind.cli.in_cell.main import SHUTDOWN_GRACE_S, run_in_cell_warden
from hivemind.wardens.deps import WardenDeps
from waggle.clock import SystemClock
from waggle.envelope import Hop, wrap
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.forage import CapacityReport
from waggle.messages.labels import Urgency
from waggle.transport.socks import FakeSocksProxy
from waggle.transport.websocket import WebSocketTransport

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
QUIET_S = 0.3  # Long enough that a connection made on purpose would have arrived by now.
LIMIT_S = "0.5"  # The offline limit a test gives the Cell: short, so clustering is seen at once.
_CLOCK = SystemClock()


@pytest.fixture
async def stand() -> AsyncIterator[TorStand]:
    """A signed loopback listener, and a fake Tor SOCKS port routing the onion name to it."""
    async with tor_stand() as made:
        yield made


async def _announced(
    arrival: asyncio.Future[WebSocketTransport],
) -> tuple[WebSocketTransport, CellReady]:
    """Await the next connection and read the three frames a Cell's announcement is made of."""
    accepted = await asyncio.wait_for(arrival, timeout=WAIT_S)
    frames = accepted.receive()
    ready = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
    capacity = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
    beat = (await asyncio.wait_for(anext(frames), timeout=WAIT_S)).payload
    assert isinstance(ready, CellReady)
    assert isinstance(capacity, CapacityReport)
    assert isinstance(beat, CellHeartbeat)
    return accepted, ready


async def _ended(stand: TorStand, accepted: WebSocketTransport, ready: CellReady) -> None:
    """Send the Cell's Warden the Queen's Shutdown, as a teardown does."""
    hop = Hop(sender=stand.hive_id, recipient=ready.warden_id, node_id=stand.node_id)
    stop = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
    await accepted.send(wrap(stop, hop, clock=_CLOCK))


async def _quiet(arrival: asyncio.Future[WebSocketTransport]) -> bool:
    """Whether no connection reached the listener for `QUIET_S`; `arrival` is left waiting."""
    done, _pending = await asyncio.wait({arrival}, timeout=QUIET_S)
    return not done


async def test_a_night_veil_cell_whose_connection_drops_redials_through_tor_alone(
    stand: TorStand, tmp_path: Path
) -> None:
    run_task = asyncio.ensure_future(run_in_cell_warden(stand.environ(tmp_path), _CLOCK))
    connections = stand.server.connections()
    third: asyncio.Future[WebSocketTransport] | None = None
    try:
        first, ready = await _announced(asyncio.ensure_future(anext(connections)))
        arrival = asyncio.ensure_future(anext(connections))
        await first.close()  # The Hive Stand's end of the connection goes.

        second, again = await _announced(arrival)

        third = asyncio.ensure_future(anext(connections))
        assert await _quiet(third)  # No further connection of any kind.
        await _ended(stand, second, again)
        await asyncio.wait_for(run_task, timeout=WAIT_S)
    finally:
        run_task.cancel()
        if third is not None:
            third.cancel()

    # The same Warden announced itself again, with its control-link checks passed again, and both
    # routes to the Hive Stand were the proxy, asked for the onion name.
    assert again.warden_id == ready.warden_id
    assert {check.name: check.has_passed for check in again.attestation} == {
        VIA_SOCKS_CHECK: True,
        HIDDEN_SERVICE_CHECK: True,
    }
    assert stand.proxy.requests == [(ONION, ONION_PORT)] * 2


async def test_a_night_veil_cell_whose_tor_dies_is_cut_off_clusters_and_returns_over_tor_alone(
    stand: TorStand, tmp_path: Path
) -> None:
    environ = stand.environ(tmp_path, HIVEMIND_MAX_OFFLINE_S=LIMIT_S)
    built: list[WardenDeps] = []
    run_task = asyncio.ensure_future(
        run_in_cell_warden(environ, _CLOCK, on_deps_built=built.append)
    )
    connections = stand.server.connections()
    port = stand.proxy.port  # What the Cell's proxy URL names, and so what Tor must come back on.
    returned = FakeSocksProxy({ONION: ("127.0.0.1", stand.server.port)})
    try:
        _first, ready = await _announced(asyncio.ensure_future(anext(connections)))
        await asyncio.wait_for(until(lambda: bool(built)), timeout=WAIT_S)
        device = built[0].device
        assert device is not None and device.outage is not None
        outage = device.outage
        arrival = asyncio.ensure_future(anext(connections))

        await stand.proxy.close()  # Tor dies; the VPN and the default route are not touched.

        await asyncio.wait_for(until(lambda: outage.cut_since is not None), timeout=WAIT_S)
        await asyncio.wait_for(until(lambda: outage.is_clustered_fully), timeout=WAIT_S)
        assert await _quiet(arrival)  # Cut off and clustered, and nothing came another way.
        await returned.start(port=port)  # Tor is back.

        second, again = await _announced(arrival)
        await asyncio.wait_for(until(lambda: outage.cut_since is None), timeout=WAIT_S)
        await _ended(stand, second, again)
        await asyncio.wait_for(run_task, timeout=WAIT_S)
    finally:
        run_task.cancel()
        await returned.close()

    # The same Warden came back, to the Queen who knew it, over the restarted Tor by the onion name.
    assert again.warden_id == ready.warden_id
    assert returned.requests == [(ONION, ONION_PORT)]


async def test_a_night_veil_cell_torn_down_while_cut_off_stops_within_its_grace(
    stand: TorStand, tmp_path: Path
) -> None:
    built: list[WardenDeps] = []
    run_task = asyncio.ensure_future(
        run_in_cell_warden(stand.environ(tmp_path), _CLOCK, on_deps_built=built.append)
    )
    connections = stand.server.connections()
    try:
        await _announced(asyncio.ensure_future(anext(connections)))
        await asyncio.wait_for(until(lambda: bool(built)), timeout=WAIT_S)
        device = built[0].device
        assert device is not None and device.outage is not None
        outage = device.outage
        await stand.proxy.close()
        await asyncio.wait_for(until(lambda: outage.cut_since is not None), timeout=WAIT_S)

        run_task.cancel()  # The backend destroys the Cell; its Queen is out of reach.
        [outcome] = await asyncio.wait_for(
            asyncio.gather(run_task, return_exceptions=True), timeout=SHUTDOWN_GRACE_S + WAIT_S
        )
    finally:
        run_task.cancel()

    assert isinstance(outcome, asyncio.CancelledError)  # Nothing else came out of its teardown.
