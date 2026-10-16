"""Tests for hivemind.cli.in_cell.borrowed.run: a colonized device's Warden against a listener.

A scripted swarm listener (a real `WebSocketServer` on loopback that signs as the Queen and
verifies as the Queen's listener would: only frames signed as the device's node pass) stands in
for the Hive Stand. The Warden under test is the real one, built by the real composition, leasing
a real temporary directory as its device.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/borrowed/run.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one). Proves the wiring and the frames the Queen sees, in order; the Warden's own
    tick, the offline link and the lease have their own suites.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.borrowed.run for the module under test.
    - tests/unit/cli/in_cell/test_main.py for the Virtual Cell's counterpart.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from builders.borrowed import SERVER_ID, BorrowedKit, make_kit
from builders.memory import make_handoff

from hivemind.cli.in_cell.borrowed import open_device_stores, run_borrowed_warden, stop_on_signals
from hivemind.cli.in_cell.borrowed.stores import DATABASE_FILE
from hivemind.manifest import read_in_cell_env
from hivemind.memory import Handoff, MemoryContext, MemoryIdentity, write_checkpoint
from hivemind.pheromone import TrailQuery
from hivemind.swarm import ShieldedSource
from hivemind.wardens.deps import WardenDeps
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import EventId
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.forage import CapacityReport
from waggle.messages.labels import AccessLevel, CombShieldLevel, Urgency
from waggle.messages.swarm import TrailSegmentSync
from waggle.transport.base import Transport
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
ATTACH_FRAMES = 4  # cell.ready, capacity report, cell.heartbeat, then the trail segment.
_CLOCK = SystemClock()


@contextlib.asynccontextmanager
async def _queen(kit: BorrowedKit) -> AsyncIterator[WebSocketServer]:
    """Serve a scripted swarm listener and point the kit's environment at it."""
    server = WebSocketServer(Codec(signer=kit.queen_signer, verifier=kit.device_verifier))
    await server.start()
    kit.environ["HIVEMIND_QUEEN_WAGGLE_URL"] = server.uri
    try:
        yield server
    finally:
        await server.close()


async def _next(receive: AsyncIterator[Envelope]) -> Envelope:
    """The next frame the Warden sent, or a failure instead of a hang."""
    return await asyncio.wait_for(anext(receive), timeout=WAIT_S)


async def _attached(
    connections: AsyncIterator[Transport],
) -> tuple[Transport, list[Envelope]]:
    """Accept a connection and read the frames a Warden's attachment is made of."""
    transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
    receive = transport.receive()
    return transport, [await _next(receive) for _ in range(ATTACH_FRAMES)]


class _Built:
    """The deps a Warden was built with, and a signal for when they exist (`on_deps_built`)."""

    def __init__(self) -> None:
        self.deps: list[WardenDeps] = []
        self.ready = asyncio.Event()

    def __call__(self, deps: WardenDeps) -> None:
        self.deps.append(deps)
        self.ready.set()


def _start(
    kit: BorrowedKit, stop: asyncio.Event, built: _Built | None = None
) -> asyncio.Task[None]:
    """Run the Warden as a task, from the environment the kit holds right now."""
    env = read_in_cell_env(kit.environ)
    return asyncio.ensure_future(run_borrowed_warden(env, _CLOCK, on_deps_built=built, stop=stop))


async def _stopped(task: asyncio.Task[None], stop: asyncio.Event) -> None:
    """Ask the Warden to stop, as the packet's SIGTERM does, and wait for it to return."""
    stop.set()
    await asyncio.wait_for(task, timeout=WAIT_S)


async def test_a_device_warden_attaches_with_four_frames_in_the_order_the_queen_expects(
    tmp_path: Path,
) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1", access_level="SCRATCH", with_server=True)
    async with _queen(kit) as server:
        stop = asyncio.Event()
        task = _start(kit, stop)
        _transport, frames = await _attached(server.connections())
        await _stopped(task, stop)

    ready, capacity, heartbeat, segment = (frame.payload for frame in frames)
    assert isinstance(ready, CellReady)
    assert (ready.cell_id, ready.warden_id) == (kit.cell_id, kit.warden_id)
    assert ready.access_level is AccessLevel.SCRATCH
    assert ready.comb_shield is CombShieldLevel.MEADOW
    assert isinstance(capacity, CapacityReport)
    declared = capacity.model_servers
    assert [report.server_id for report in declared] == [SERVER_ID]
    assert declared[0].sources == ()  # Declared, not started.
    assert isinstance(heartbeat, CellHeartbeat) and heartbeat.cell_id == kit.cell_id
    assert isinstance(segment, TrailSegmentSync)  # The trail is shipped as it attaches.
    # Every frame came from the Warden, signed as the device's node (the listener verified it).
    assert {frame.sender for frame in frames} == {kit.warden_id}
    assert {frame.node_id for frame in frames} == {kit.node_id}
    assert {frame.recipient for frame in frames} == {kit.hive_id}


async def test_the_final_trail_chunk_is_signed_with_the_devices_key(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    async with _queen(kit) as server:
        stop = asyncio.Event()
        task = _start(kit, stop)
        _transport, frames = await _attached(server.connections())
        await _stopped(task, stop)

    segment = frames[3].payload
    assert isinstance(segment, TrailSegmentSync)
    assert segment.final and segment.segment_signature is not None  # Merged only with this.


async def test_a_signal_stops_the_warden_and_leaves_the_device_as_it_was_found(
    tmp_path: Path,
) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    async with _queen(kit) as server:
        stop = asyncio.Event()
        task = _start(kit, stop)
        await _attached(server.connections())
        await _stopped(task, stop)  # What the packet's SIGTERM becomes.

    # The stores were closed last, after the Warden stopped: no WAL writer is left behind for the
    # packet's restart of the process to recover from.
    assert not (kit.state_dir / f"{DATABASE_FILE}-wal").exists()
    stores = await open_device_stores(kit.state_dir, _CLOCK)
    kinds = [event.kind for event in await stores.trail.query(TrailQuery())]
    assert kinds.count("cell.leased") == kinds.count("cell.released") == 1
    assert "warden.stopped" in kinds
    assert not any((kit.state_dir / "scratch").iterdir())  # Its lease's scratch is gone.
    await stores.close()


async def test_a_queen_sent_shutdown_ends_the_warden_too(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    async with _queen(kit) as server:
        task = _start(kit, asyncio.Event())
        transport, frames = await _attached(server.connections())
        hop = Hop(sender=kit.hive_id, recipient=frames[0].sender, node_id=kit.queen_node_id)
        cue = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")

        await transport.send(wrap(cue, hop, clock=_CLOCK))

        await asyncio.wait_for(task, timeout=WAIT_S)  # It stopped itself and returned.


async def test_a_dropped_link_is_mended_and_the_cell_announced_again(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    async with _queen(kit) as server:
        stop = asyncio.Event()
        task = _start(kit, stop)
        connections = server.connections()
        first, _frames = await _attached(connections)

        await first.close()  # The listener drops the connection.
        _second, again = await _attached(connections)

        await _stopped(task, stop)
    ready, capacity, heartbeat = (frame.payload for frame in again[:3])
    assert isinstance(ready, CellReady) and ready.warden_id == kit.warden_id  # The same bee.
    assert isinstance(capacity, CapacityReport) and isinstance(heartbeat, CellHeartbeat)


async def test_a_stop_while_the_queen_is_unreachable_ends_the_dial(tmp_path: Path) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")  # Nothing listens: the link dials with backoff.
    stop = asyncio.Event()
    task = _start(kit, stop)
    await asyncio.sleep(0.3)  # Let the first attempt fail and the backoff begin.
    assert not task.done()

    await _stopped(task, stop)  # No Warden was started, so none needs stopping.

    assert not (kit.state_dir / f"{DATABASE_FILE}-wal").exists()  # The stores are closed too.


async def test_a_propolis_device_attaches_with_its_checks_and_verifies_its_own_leases(
    tmp_path: Path,
) -> None:
    # The whole of colonizing a Propolis device from the device's side: the packet hands the
    # Warden the node's tier, the Warden accepts it, announces with the tunnel checks it made of
    # its own machine (the wire refuses a tiered CellReady without them), and leases the machine
    # only through the source that verifies the tunnel first, as the Hive Stand did for it.
    kit = make_kit(tmp_path, "ws://127.0.0.1:1", access_level="FULL")
    kit.environ["HIVEMIND_COMB_SHIELD"] = "PROPOLIS"
    built = _Built()
    async with _queen(kit) as server:
        stop = asyncio.Event()
        task = _start(kit, stop, built)
        _transport, frames = await _attached(server.connections())
        await asyncio.wait_for(built.ready.wait(), timeout=WAIT_S)
        await _stopped(task, stop)

    ready, _capacity, heartbeat = (frame.payload for frame in frames[:3])
    assert isinstance(ready, CellReady) and isinstance(heartbeat, CellHeartbeat)
    assert ready.comb_shield is CombShieldLevel.PROPOLIS
    assert ready.attestation  # Made of this machine's own routes: whatever they say, they are said.
    assert heartbeat.is_shield_verified is all(check.has_passed for check in ready.attestation)
    [deps] = built.deps
    assert isinstance(deps.source, ShieldedSource)  # A lease waits for the tunnel checks.


def _context(deps: WardenDeps, kit: BorrowedKit) -> MemoryContext:
    """The context the Warden writes memory with: its own store, its identity, its clock."""
    identity = MemoryIdentity(hive_id=kit.hive_id, node_id=kit.node_id, actor=str(kit.warden_id))
    return MemoryContext(store=deps.memory, identity=identity, clock=_CLOCK)


async def _written(
    kit: BorrowedKit, connections: AsyncIterator[Transport], handoff: Handoff
) -> EventId:
    """Run a Warden, have it write `handoff` as a pausing sub-bee would, stop it; return its id."""
    built, stop = _Built(), asyncio.Event()
    task = _start(kit, stop, built)
    await _attached(connections)
    await asyncio.wait_for(built.ready.wait(), timeout=WAIT_S)
    ref = await write_checkpoint(handoff, None, _context(built.deps[0], kit))
    await _stopped(task, stop)
    return ref.event_id


async def test_a_handoff_survives_the_wardens_restart(tmp_path: Path) -> None:
    # The first run pauses a task and writes its Handoff; the second, a different process in
    # production, is built over the same state directory and finds it there.
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    handoff, other = make_handoff(_CLOCK), make_handoff(_CLOCK, goal="Another.")
    async with _queen(kit) as server:
        connections = server.connections()
        first = await _written(kit, connections, handoff)
        second = await _written(kit, connections, other)

    stores = await open_device_stores(kit.state_dir, _CLOCK)
    # Both runs' Handoffs are in the one store: the second run opened what the first left.
    restored = [(await stores.memory.get_handoff(ref))[0] for ref in (first, second)]
    assert restored == [handoff, other]


@pytest.fixture
def _restored_signals() -> Iterator[None]:
    """Put the process's SIGTERM handler back as it was, whatever a test did to it."""
    before = signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGTERM, before)


async def test_a_sigterm_sets_the_stop_event_and_the_old_handler_comes_back(
    _restored_signals: None,
) -> None:
    before = signal.getsignal(signal.SIGTERM)
    stop = asyncio.Event()

    with stop_on_signals(asyncio.get_running_loop(), stop):
        assert signal.getsignal(signal.SIGTERM) is not before
        # What the packet's WITHDRAW sends, raised rather than sent with os.kill: on Windows that
        # is TerminateProcess and would end pytest itself, while raise() runs the handler.
        signal.raise_signal(signal.SIGTERM)
        await asyncio.wait_for(stop.wait(), timeout=WAIT_S)

    assert signal.getsignal(signal.SIGTERM) is before
