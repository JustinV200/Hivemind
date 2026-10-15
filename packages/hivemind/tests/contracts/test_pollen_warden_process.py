"""Test the real Warden process a Pollen Packet starts on a colonized device, until it is withdrawn.

The unit suites prove each end alone: pollen's with a stub program that records its environment,
hivemind's with the Warden run inside the test process. This is the one test with the real thing on
both ends of the process boundary. A packet's `WardenKeeper` starts this checkout's own
`hivemind-in-cell` console script, in a process group of its own, with the environment
`pollen.bootstrap` builds and the key the packet keeps; the Warden is a separate Python process
that reads that environment, opens its SQLite stores, dials a scripted swarm listener (a real
`WebSocketServer` on loopback that signs as the Queen and verifies as the Queen's listener would)
and announces the Cell. Withdrawing it sends SIGTERM to the group, as the Queen's WITHDRAW does,
and the Warden must stop its work, give the device back and leave, well within the packet's grace,
without being killed.

Fits into the Hive:
    Contract test (codingrules section 14.3): one wire between two packages, with a real process on
    it. Mirrors nothing in src/; the modules it joins are `pollen.bootstrap.warden` and
    `hivemind.cli.in_cell.main`, reached through the console script the package installs.

Key invariants:
    - The Warden is the real console script of this checkout, started by pollen's own code with
      pollen's own environment; nothing is adjusted between the two.
    - Every frame the listener reads was signed as the device's node (the listener verified it).
    - The Warden leaves by SIGTERM and not by SIGKILL: its trail says it stopped, and its lease,
      if it held one, is released.
    - The Warden is ended before the test returns, whatever happens inside it.

See Also:
    - contracts.test_pollen_warden_env for the environment alone, without a process.
    - hivemind.cli.in_cell.borrowed.README for what the Queen sees on a connection.
    - pollen.bootstrap.warden for the keeper that starts and stops the process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path

import pytest
from builders.pollen_packet import Packet, install_runtime, make_packet, warden_script

from hivemind.cli.in_cell.borrowed import open_device_stores
from hivemind.pheromone import TrailQuery
from pollen.bootstrap import BootstrapTimings, WardenKeeper
from pollen.executors import ExecutorDeps, TaskScope
from pollen.lease import LeaseTable
from pollen.platform import ProcessRef, select_shim
from pollen.state import StateStore
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.messages.base import WaggleMessage
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.forage import CapacityReport
from waggle.messages.labels import AccessLevel
from waggle.messages.swarm import RuntimeLevel, TrailSegmentSync
from waggle.signing import Ed25519Verifier
from waggle.transport.websocket_server import WebSocketServer

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the runtime's programs are POSIX scripts and the stop a signal"
)

ATTACH_WAIT_S = 90.0  # A cold import of the whole runtime on a loaded runner is the cost.
STOP_GRACE_S = 30.0  # The packet's own grace; the Warden is expected to leave far sooner.
ATTACH_FRAMES = 4  # cell.ready, capacity report, cell.heartbeat, then the trail segment.
LOG_TAIL_CHARS = 4000  # How much of the Warden's log a failure shows.


class _Mute:
    """A reporter for a keeper whose packet has no Queen to answer: nothing is sent."""

    async def send(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> bool:
        """Report that there was no connection."""
        return False

    async def send_durable(
        self, payload: WaggleMessage, *, to: str, correlation_id: str | None = None
    ) -> None:
        """Drop the frame; no test here looks for an answer."""


def _keeper(packet: Packet) -> tuple[WardenKeeper, ExecutorDeps]:
    """A keeper over the packet's data directory, with the real platform shim and clock."""
    deps = ExecutorDeps(
        StateStore(packet.layout.state, packet.state),
        LeaseTable(packet.layout.lease_table),
        _Mute(),
        SystemClock(),
        select_shim(),
        packet.layout,
        TaskScope(),
    )
    timings = BootstrapTimings(settle_s=1.0, stop_grace_s=STOP_GRACE_S, watch_interval_s=1.0)
    return WardenKeeper(deps, timings), deps


def _log_tail(packet: Packet) -> str:
    """The end of the Warden's log, for a failure that would otherwise be a bare timeout."""
    try:
        return packet.layout.warden_log.read_text(encoding="utf-8", errors="replace")[
            -LOG_TAIL_CHARS:
        ]
    except OSError:
        return "(no log)"


def _kill_group(pid: int) -> None:
    """Kill the group a Warden that outlived its test leads; nothing if it has gone already."""
    # The module skips on Windows, but mypy checks it for Windows too, where killpg and SIGKILL
    # do not exist. The else is load-bearing: mypy skips the branch a platform check rules out,
    # and reports whatever follows an early return as unreachable.
    if sys.platform == "win32":
        return
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)


async def test_the_warden_a_packet_starts_attaches_to_the_queen_and_leaves_the_device_as_found(
    tmp_path: Path,
) -> None:
    script = warden_script()
    if script is None:
        pytest.skip("this checkout has no installed hivemind-in-cell console script")
    packet = make_packet(tmp_path, level=AccessLevel.SCRATCH)
    state = packet.state
    install_runtime(packet.layout, select_shim(), script)
    keeper, deps = _keeper(packet)
    verifier = Ed25519Verifier({state.node_id: packet.device_signer.public_key_bytes})
    server = WebSocketServer(Codec(signer=packet.queen_signer, verifier=verifier))
    await server.start()
    try:
        record = await keeper.start(server.uri)
        try:
            transport = await asyncio.wait_for(anext(server.connections()), ATTACH_WAIT_S)
            receive = transport.receive()
            frames = [
                await asyncio.wait_for(anext(receive), ATTACH_WAIT_S) for _ in range(ATTACH_FRAMES)
            ]
        except TimeoutError:
            pytest.fail(f"The Warden never attached. Its log ends:\n{_log_tail(packet)}")
        await keeper.withdraw()  # SIGTERM to the group; the grace is far longer than it needs.
    finally:
        left = deps.state.current.warden
        if left is not None:  # Only a test that failed gets here with the Warden still running.
            _kill_group(left.pid)
            await asyncio.sleep(0.1)  # Let the loop's child watcher collect the process.
        await server.close()

    # What the Queen saw, signed as the device's node, from the Warden she knows.
    ready, capacity, heartbeat, segment = (frame.payload for frame in frames)
    assert isinstance(ready, CellReady) and ready.access_level is AccessLevel.SCRATCH
    assert (ready.cell_id, ready.warden_id) == (state.cell_id, state.warden_id)
    assert isinstance(capacity, CapacityReport) and isinstance(heartbeat, CellHeartbeat)
    assert isinstance(segment, TrailSegmentSync)
    assert {frame.sender for frame in frames} == {state.warden_id}
    assert {frame.node_id for frame in frames} == {state.node_id}

    # What the device looks like afterwards: the Warden is gone by its own hand, and says so.
    assert not deps.shim.group_alive(ProcessRef(record.pid, record.created))
    assert (deps.state.current.level, deps.state.current.warden) == (RuntimeLevel.GATEWAY, None)
    stores = await open_device_stores(packet.layout.warden_state, SystemClock())
    kinds = [event.kind for event in await stores.trail.query(TrailQuery())]
    assert "warden.stopped" in kinds  # It was asked to stop and did, rather than being killed.
    assert kinds.count("cell.leased") == kinds.count("cell.released")
    assert "Traceback" not in _log_tail(packet)
