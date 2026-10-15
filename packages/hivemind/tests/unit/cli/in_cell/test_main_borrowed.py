"""Tests for hivemind.cli.in_cell.main on a colonized device: the lease mode picks the Warden.

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/main.py (codingrules section 3), split by feature (14.2) from
    test_main.py, which holds the Virtual Cell's path. `HIVEMIND_CELL_LEASE=borrowed` makes the
    same entry point run the Warden of a Swarm device (roadmap step 11.6a); the whole of that
    Warden is tested in borrowed/, so these tests prove only the switch and the process wrapper.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.main for the module under test.
    - borrowed/test_run.py for the device Warden itself.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Iterator
from pathlib import Path

import pytest
from builders.borrowed import BorrowedKit, make_kit

from hivemind.cli.in_cell.main import _run_process, run_in_cell_warden
from hivemind.common.errors import ConfigurationError
from hivemind.manifest import read_in_cell_env
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.messages.cell.status import CellReady
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 10.0  # Bounds every await that could hang; loopback answers in milliseconds.
_CLOCK = SystemClock()


@pytest.fixture
def _restored_signals() -> Iterator[None]:
    """Put the process's signal handlers back as they were, whatever a test did to them."""
    before = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    yield
    for number, handler in before.items():
        signal.signal(number, handler)


async def _listener(kit: BorrowedKit) -> WebSocketServer:
    """Serve a scripted swarm listener and point the kit's environment at it."""
    server = WebSocketServer(Codec(signer=kit.queen_signer, verifier=kit.device_verifier))
    await server.start()
    kit.environ["HIVEMIND_QUEEN_WAGGLE_URL"] = server.uri
    return server


async def _first_frame_is_ready(server: WebSocketServer, kit: BorrowedKit) -> None:
    """Accept the Warden's connection and check its first frame is the device's `cell.ready`."""
    transport = await asyncio.wait_for(anext(server.connections()), timeout=WAIT_S)
    first = await asyncio.wait_for(anext(transport.receive()), timeout=WAIT_S)
    assert isinstance(first.payload, CellReady)
    assert (first.payload.cell_id, first.sender, first.node_id) == (
        kit.cell_id,
        kit.warden_id,
        kit.node_id,
    )


async def test_a_borrowed_lease_mode_runs_the_device_warden_not_a_virtual_cells(
    tmp_path: Path,
) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    server = await _listener(kit)
    try:
        stop = asyncio.Event()
        task = asyncio.ensure_future(run_in_cell_warden(kit.environ, _CLOCK, stop=stop))

        await _first_frame_is_ready(server, kit)  # Signed as the device, under the given ids.
        stop.set()
        await asyncio.wait_for(task, timeout=WAIT_S)
    finally:
        await server.close()
    assert (kit.state_dir / "warden.sqlite").is_file()  # Its stores are the durable ones.


async def test_a_sigterm_to_the_process_stops_a_borrowed_warden_cleanly(
    tmp_path: Path, _restored_signals: None
) -> None:
    kit = make_kit(tmp_path, "ws://127.0.0.1:1")
    server = await _listener(kit)
    try:
        task = asyncio.ensure_future(_run_process(kit.environ, read_in_cell_env(kit.environ)))
        await _first_frame_is_ready(server, kit)

        # What the packet's WITHDRAW sends, raised rather than sent with os.kill: on Windows that
        # is TerminateProcess and would end pytest itself, while raise() runs the handler.
        signal.raise_signal(signal.SIGTERM)

        await asyncio.wait_for(task, timeout=WAIT_S)  # Stopped, device left as found, returned.
    finally:
        await server.close()


async def test_a_lease_mode_nobody_knows_stops_the_process_before_it_runs_anything(
    tmp_path: Path,
) -> None:
    environ = {**make_kit(tmp_path, "ws://127.0.0.1:1").environ, "HIVEMIND_CELL_LEASE": "rented"}

    with pytest.raises(ConfigurationError, match="HIVEMIND_CELL_LEASE"):
        await _run_process(environ, read_in_cell_env(environ))
