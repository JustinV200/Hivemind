"""Tests for hivemind.cli.in_cell.redial.dial: the dialer, the announcer and the after-step.

The dialer is what makes "no fallback" true of a Night Veil Cell's link, so it is tested against a
fake Tor and a real listener on the same loopback: it reaches the listener through the proxy by
the onion name, and when the proxy is down or refusing its attempt fails and the listener, which it
could have dialled directly, sees nothing. The announcer must say what the Cell's first connection
said, in the order the Queen's readiness gate reads it, on every connection; the after-step must
treat the first connection as no reconnection at all.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/redial/dial.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/unit/cli/in_cell/test_main_night_veil_redial.py for the whole entry point.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from builders.tor_stand import ONION, ONION_PORT, TorStand, tor_stand

from hivemind.cell import CellIdentity
from hivemind.cli.in_cell.config import InCellRuntimeConfig, build_runtime_config
from hivemind.cli.in_cell.link import HIDDEN_SERVICE_CHECK, VIA_SOCKS_CHECK
from hivemind.cli.in_cell.redial import AfterRedial, CellAnnouncer, ProxyDialer
from hivemind.manifest import read_in_cell_env
from hivemind.pheromone import MemoryPheromoneTrail
from hivemind.wardens.spawn import InCellSpawnSource
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.errors import ConnectFailedError
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.forage import CapacityReport, CapacityTrigger
from waggle.transport.socks import FakeSocksBehaviour, FakeSocksProxy

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
QUIET_S = 0.3  # Long enough that a connection made on purpose would have arrived by now.
_CLOCK = SystemClock()


@pytest.fixture
async def stand() -> AsyncIterator[TorStand]:
    """A signed loopback listener, and a fake Tor SOCKS port routing the onion name to it."""
    async with tor_stand() as made:
        yield made


def _config(stand: TorStand, tmp_path: Path, **extra: str) -> InCellRuntimeConfig:
    """The runtime config of a Night Veil Cell dialling `stand` through its Tor."""
    return build_runtime_config(read_in_cell_env(stand.environ(tmp_path, **extra)), _CLOCK)


def _codec(config: InCellRuntimeConfig) -> Codec:
    """The Codec the Cell's link signs and verifies with."""
    return Codec(signer=config.signer, verifier=config.verifier)


class _Recorder:
    """A Transport that keeps what was sent on it and delivers nothing."""

    def __init__(self) -> None:
        self.sent: list[Envelope] = []

    async def send(self, envelope: Envelope) -> None:
        """Keep `envelope`."""
        self.sent.append(envelope)


def _announcer(config: InCellRuntimeConfig) -> CellAnnouncer:
    """An announcer over the one-Cell source this config describes."""
    identity = CellIdentity(
        hive_id=config.hive_id, node_id=config.node_id, actor=str(config.warden_id)
    )
    trail = MemoryPheromoneTrail(_CLOCK)
    return CellAnnouncer(
        InCellSpawnSource(config.spawn_config, identity, trail, _CLOCK), config, _CLOCK
    )


# ──────────────────────────────────────────────────────────────────────────────
# The dialer: through the proxy, and through nothing else.
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_dialer_reaches_the_onion_service_through_the_proxy_by_name(
    stand: TorStand, tmp_path: Path
) -> None:
    config = _config(stand, tmp_path)
    arrival = asyncio.ensure_future(anext(stand.server.connections()))

    transport = await ProxyDialer(config, _codec(config), _CLOCK).dial()
    try:
        await asyncio.wait_for(arrival, timeout=WAIT_S)
    finally:
        await transport.close()

    assert stand.proxy.requests == [(ONION, ONION_PORT)]  # Asked for by name, never resolved.


async def test_the_dialer_cannot_be_built_without_a_proxy(stand: TorStand, tmp_path: Path) -> None:
    config = dataclasses.replace(_config(stand, tmp_path), socks_proxy_url=None)

    with pytest.raises(ValueError, match="SOCKS proxy"):
        ProxyDialer(config, _codec(config), _CLOCK)


async def test_a_dial_whose_proxy_is_down_fails_and_reaches_nothing_else(
    stand: TorStand, tmp_path: Path
) -> None:
    config = _config(stand, tmp_path)
    dialer = ProxyDialer(config, _codec(config), _CLOCK)
    arrival = asyncio.ensure_future(anext(stand.server.connections()))
    await stand.proxy.close()  # Tor is gone; the listener is still right there on loopback.

    with pytest.raises(ConnectFailedError, match="through the SOCKS proxy"):
        await asyncio.wait_for(dialer.dial(), timeout=WAIT_S)
    done, _pending = await asyncio.wait({arrival}, timeout=QUIET_S)
    arrival.cancel()

    assert not done  # The attempt failed; it did not try the listener some other way.


async def test_a_dial_whose_proxy_refuses_fails_and_reaches_nothing_else(
    stand: TorStand, tmp_path: Path
) -> None:
    refusing = FakeSocksProxy(behaviour=FakeSocksBehaviour(refuse_with=0xF0))
    await refusing.start()
    config = _config(stand, tmp_path, HIVEMIND_SOCKS_PROXY_URL=refusing.url())
    dialer = ProxyDialer(config, _codec(config), _CLOCK)
    arrival = asyncio.ensure_future(anext(stand.server.connections()))
    try:
        with pytest.raises(ConnectFailedError, match="descriptor not found"):
            await asyncio.wait_for(dialer.dial(), timeout=WAIT_S)
        done, _pending = await asyncio.wait({arrival}, timeout=QUIET_S)
    finally:
        arrival.cancel()
        await refusing.close()

    assert not done
    assert refusing.requests == [(ONION, ONION_PORT)]  # It asked the proxy once, for the onion.
    assert stand.proxy.requests == []


# ──────────────────────────────────────────────────────────────────────────────
# The announcer: what the Cell says on a fresh connection.
# ──────────────────────────────────────────────────────────────────────────────


async def test_the_announcer_sends_ready_then_capacity_then_a_heartbeat(
    stand: TorStand, tmp_path: Path
) -> None:
    config = _config(stand, tmp_path)
    recorder = _Recorder()

    await _announcer(config).announce(recorder)  # type: ignore[arg-type]

    ready, capacity, beat = (envelope.payload for envelope in recorder.sent)
    assert isinstance(ready, CellReady)
    assert (ready.cell_id, ready.warden_id) == (config.spawn_config.cell_id, config.warden_id)
    assert {check.name: check.has_passed for check in ready.attestation} == {
        VIA_SOCKS_CHECK: True,
        HIDDEN_SERVICE_CHECK: True,
    }
    assert isinstance(capacity, CapacityReport)
    assert capacity.max_sub_bees == config.spawn_config.capacity.max_sub_bees
    assert isinstance(beat, CellHeartbeat)
    assert beat.is_shield_verified  # Every control-link check passed.
    # Every frame is the Warden's, to the Queen, from the Cell's own node.
    assert {(e.sender, e.recipient, e.node_id) for e in recorder.sent} == {
        (config.warden_id, config.hive_id, config.node_id)
    }


async def test_the_first_announcement_is_the_provisioning_and_the_rest_are_reconnections(
    stand: TorStand, tmp_path: Path
) -> None:
    announcer = _announcer(_config(stand, tmp_path))
    first, second = _Recorder(), _Recorder()

    await announcer.announce(first)  # type: ignore[arg-type]
    await announcer.announce(second)  # type: ignore[arg-type]

    triggers = [
        recorder.sent[1].payload.trigger
        for recorder in (first, second)
        if isinstance(recorder.sent[1].payload, CapacityReport)
    ]
    assert triggers == [CapacityTrigger.PROVISIONED, CapacityTrigger.RECONNECTED]


# ──────────────────────────────────────────────────────────────────────────────
# The after-step: the first connection is no reconnection.
# ──────────────────────────────────────────────────────────────────────────────


class _Steps:
    """An AfterReconnect that counts its calls."""

    def __init__(self) -> None:
        self.outages: list[float] = []

    async def reconnected(self, outage_s: float) -> None:
        """Keep how long the link was down."""
        self.outages.append(outage_s)


async def test_the_first_connection_records_nothing_and_every_later_one_does() -> None:
    steps = _Steps()
    after = AfterRedial(steps)

    await after.reconnected(0.0)  # The Cell has just come up.
    await after.reconnected(12.5)  # Tor dropped it, and it came back.
    await after.reconnected(3.0)

    assert steps.outages == [12.5, 3.0]
