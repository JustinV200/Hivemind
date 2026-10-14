"""Tests for hivemind.cli.in_cell.main as a Night Veil Cell: its link goes only through Tor.

Roadmap step 10.3a's exit bullet, run for real: a Cell whose bootstrap names NIGHT_VEIL, the
Queen's onion service and the Tor SOCKS proxy on its own loopback dials the Queen only through that
proxy (here a `FakeSocksProxy` playing Tor, routing the onion name to a real loopback
`WebSocketServer` standing in for the Hive Stand), announces NIGHT_VEIL with its control-link
checks passed, and builds a Warden whose floors see NIGHT_VEIL and refuse `net` to the Hive
Stand's onion by name. A proxy that refuses leaves the Cell unconnected, asking the proxy again
and again: it never dials directly. What the Cell does when Tor dies under it, once connected, is
in `test_main_night_veil_redial`.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/main.py (codingrules 5.1: split by feature from
    test_main.py).

Key invariants:
    - None: this module holds tests only.

See Also:
    - waggle.transport.socks for the proxy exchange and FakeSocksProxy.
    - tests/builders/tor_stand.py for the Hive Stand's side of the link.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.cell_listener import until
from builders.tor_stand import ONION, ONION_PORT, TorStand, tor_stand

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
from waggle.envelope import Hop, wrap
from waggle.ids import new_worker_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.labels import CombShieldLevel as WireCombShieldLevel
from waggle.messages.labels import Urgency
from waggle.messages.task import WorkerRole
from waggle.transport.socks import FakeSocksBehaviour, FakeSocksProxy

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
_CLOCK = SystemClock()


@pytest.fixture
async def stand() -> AsyncIterator[TorStand]:
    """A signed loopback listener, and a fake Tor SOCKS port routing the onion name to it."""
    async with tor_stand() as made:
        yield made


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
    stand: TorStand, tmp_path: Path
) -> None:
    built: list[WardenDeps] = []
    run_task = asyncio.ensure_future(
        run_in_cell_warden(stand.environ(tmp_path), _CLOCK, on_deps_built=built.append)
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
    assert stand.proxy.requests == [(ONION, ONION_PORT)]
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
    assert _refused_net(deps, ONION) == "guard.state_floor.loopback"


async def test_a_night_veil_cell_whose_proxy_refuses_keeps_asking_it_and_never_dials_directly(
    stand: TorStand, tmp_path: Path
) -> None:
    refusing = FakeSocksProxy(behaviour=FakeSocksBehaviour(refuse_with=0xF0))
    await refusing.start()
    environ = stand.environ(tmp_path, HIVEMIND_SOCKS_PROXY_URL=refusing.url())
    arrival = asyncio.ensure_future(anext(stand.server.connections()))
    run_task = asyncio.ensure_future(run_in_cell_warden(environ, _HurriedClock()))
    try:
        await asyncio.wait_for(until(lambda: len(refusing.requests) >= 3), timeout=WAIT_S)
        assert not run_task.done()  # Tor not answering is waited out, not given up on.
        assert not arrival.done()  # And nothing reached the Hive Stand by any other way.
    finally:
        for task in (run_task, arrival):
            task.cancel()
        await asyncio.gather(run_task, arrival, return_exceptions=True)
        await refusing.close()

    assert set(refusing.requests) == {(ONION, ONION_PORT)}  # It asked the proxy, only the proxy.
    assert stand.proxy.requests == []  # The working route was never tried behind its back.
