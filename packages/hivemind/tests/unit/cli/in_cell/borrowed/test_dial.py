"""Tests for hivemind.cli.in_cell.borrowed.dial: the dialer, the announcer and the late trail sync.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/dial.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.dial for the module under test.
    - test_run.py for the same collaborators over a real socket, in a whole Warden.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from builders.borrowed import SERVER_ID, make_kit, watch

from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cell.local import HiveStandSource
from hivemind.cell.source import CellIdentity
from hivemind.cli.in_cell.borrowed import (
    DeviceAnnouncer,
    DeviceDialer,
    DeviceIdentity,
    LateTrailSync,
    build_borrowed_config,
)
from hivemind.manifest import read_in_cell_env
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock, SystemClock
from waggle.codec import Codec
from waggle.errors import ConnectFailedError
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.forage import CapacityReport, CapacityTrigger, ModelServerReport
from waggle.messages.labels import AccessLevel, CombShieldLevel
from waggle.transport.memory import MemoryTransport
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 5.0  # Bounds every await that could hang; in-memory and loopback answer in milliseconds.


async def _announcer(tmp_path: Path, servers: bool, roots: tuple[str, ...] = ()) -> DeviceAnnouncer:
    """A device announcer over the real lease source of a kit (FULL access, MEADOW tier)."""
    kit = make_kit(tmp_path, "ws://127.0.0.1:8720", access_level="FULL", with_server=servers)
    if roots:
        watch(kit, *roots)
    config = build_borrowed_config(read_in_cell_env(kit.environ), SystemClock())
    clock = FakeClock()
    runtime = config.runtime
    source = HiveStandSource(
        config.cell,
        CellIdentity(
            hive_id=runtime.hive_id, node_id=runtime.node_id, actor=str(runtime.warden_id)
        ),
        MemoryPheromoneTrail(clock),
        clock,
        InMemoryLeavingsStore(MemoryPheromoneTrail(clock)),
    )
    return DeviceAnnouncer(
        DeviceIdentity(runtime.warden_id, runtime.hive_id, runtime.node_id),
        source,
        config.llm.servers,
        clock,
        config.watch_roots,
    )


async def _announced(tmp_path: Path, servers: bool, roots: tuple[str, ...] = ()) -> list[object]:
    """Announce over an in-memory pair and return the payloads the Queen's end received."""
    announcer = await _announcer(tmp_path, servers, roots)
    warden_end, queen_end = MemoryTransport.pair(Codec(), Codec())
    await announcer.announce(warden_end)
    receive = queen_end.receive()
    return [(await asyncio.wait_for(anext(receive), timeout=WAIT_S)).payload for _ in range(3)]


async def test_a_fresh_connection_is_announced_ready_then_capacity_then_heartbeat(
    tmp_path: Path,
) -> None:
    payloads = await _announced(tmp_path, servers=False)

    assert [type(payload) for payload in payloads] == [CellReady, CapacityReport, CellHeartbeat]


async def test_the_ready_frame_carries_the_level_and_tier_the_queen_granted(
    tmp_path: Path,
) -> None:
    ready, _capacity, _heartbeat = await _announced(tmp_path, servers=False)

    assert isinstance(ready, CellReady)
    assert ready.access_level is AccessLevel.FULL  # The device's level, not a Virtual Cell's.
    assert ready.comb_shield is CombShieldLevel.MEADOW
    assert ready.attestation == ()


async def test_the_ready_frame_carries_the_directories_the_warden_was_started_to_watch(
    tmp_path: Path,
) -> None:
    roots = ("/var/log/app", "/srv/data")

    ready, _capacity, _heartbeat = await _announced(tmp_path, servers=False, roots=roots)

    assert isinstance(ready, CellReady)
    assert ready.watch_roots == roots  # As the packet handed them over: the Queen compares them.


async def test_a_warden_started_with_no_directories_says_so(tmp_path: Path) -> None:
    ready, _capacity, _heartbeat = await _announced(tmp_path, servers=False)

    assert isinstance(ready, CellReady) and ready.watch_roots == ()


async def test_the_capacity_report_lists_the_declared_servers_and_starts_none(
    tmp_path: Path,
) -> None:
    _ready, capacity, _heartbeat = await _announced(tmp_path, servers=True)

    assert isinstance(capacity, CapacityReport)
    assert capacity.model_servers == (ModelServerReport(server_id=SERVER_ID, sources=()),)
    assert capacity.trigger is CapacityTrigger.RECONNECTED
    assert capacity.usage.model_vram_bytes == 0  # Nothing is loaded: the server is only declared.


async def test_the_capacity_report_of_a_device_that_declares_nothing_lists_nothing(
    tmp_path: Path,
) -> None:
    _ready, capacity, _heartbeat = await _announced(tmp_path, servers=False)

    assert isinstance(capacity, CapacityReport)
    assert capacity.model_servers == ()


async def test_each_announcement_describes_the_cell_as_it_is_then(tmp_path: Path) -> None:
    announcer = await _announcer(tmp_path, servers=False)
    warden_end, queen_end = MemoryTransport.pair(Codec(), Codec())

    await announcer.announce(warden_end)  # A second connection, as after an outage.
    await announcer.announce(warden_end)

    receive = queen_end.receive()
    kinds = [type((await asyncio.wait_for(anext(receive), WAIT_S)).payload) for _ in range(6)]
    assert kinds == [CellReady, CapacityReport, CellHeartbeat] * 2


async def test_a_dial_that_cannot_connect_raises_a_transport_error_for_the_links_backoff(
    tmp_path: Path,
) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")  # Nothing listens on port 1.
    dialer = DeviceDialer("ws://127.0.0.1:1", None, Codec(signer=kit.device_signer), SystemClock())

    with pytest.raises(ConnectFailedError):
        await asyncio.wait_for(dialer.dial(), timeout=WAIT_S)


async def test_a_dial_that_connects_returns_a_live_signed_connection(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    server = WebSocketServer(Codec(signer=kit.queen_signer, verifier=kit.device_verifier))
    await server.start()
    try:
        connections = server.connections()
        dialer = DeviceDialer(server.uri, None, Codec(signer=kit.device_signer), SystemClock())

        transport = await asyncio.wait_for(dialer.dial(), timeout=WAIT_S)
        await asyncio.wait_for(anext(connections), timeout=WAIT_S)

        assert transport.is_connected
        await transport.close()
    finally:
        await server.close()


def test_a_pin_that_is_not_a_fingerprint_is_refused_when_the_dialer_is_built(
    tmp_path: Path,
) -> None:
    kit = make_kit(tmp_path, "wss://hive.example.org:8443")

    with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
        DeviceDialer(
            "wss://hive.example.org:8443", "AB:CD", Codec(signer=kit.device_signer), SystemClock()
        )


class _CountingSync:
    """A TrailSync that counts how often it was asked to ship, and to ship again."""

    def __init__(self, result: bool) -> None:
        self.calls = 0
        self.rewound: list[timedelta] = []
        self._result = result

    async def sync(self) -> bool:
        self.calls += 1
        return self._result

    def rewind(self, window: timedelta) -> None:
        self.rewound.append(window)


async def test_a_late_trail_sync_ships_nothing_until_it_is_bound() -> None:
    late = LateTrailSync()
    assert await late.sync() is True  # Nothing was lost by not shipping yet.

    target = _CountingSync(result=False)
    late.bind(target)

    assert await late.sync() is False  # What the real sync said, link trouble included.
    assert target.calls == 1

    late.bind(None)
    assert await late.sync() is True
    assert target.calls == 1


def test_a_late_trail_sync_hands_a_rewind_to_the_sync_it_is_bound_to_and_to_nothing_before() -> (
    None
):
    late = LateTrailSync()
    late.rewind(timedelta(seconds=80))  # Unbound: there is nothing shipped to take back.

    target = _CountingSync(result=True)
    late.bind(target)
    late.rewind(timedelta(seconds=80))

    assert target.rewound == [timedelta(seconds=80)]
