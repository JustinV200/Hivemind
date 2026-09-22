"""Tests for hivemind.cli.in_cell.main: run_in_cell_warden, the entry point's async body.

An end-to-end smoke test of the whole composition root (roadmap step 5.3): read a real
`HIVEMIND_*` environment, build every collaborator (`hivemind.cli.in_cell.config.
build_runtime_config`, `hivemind.wardens.spawn.in_cell.InCellSpawnSource`,
`hivemind.cli.in_cell.deps.build_in_cell_warden_deps`), connect out to a real `WebSocketServer` on
loopback, send CellReady/CapacityReport/CellHeartbeat, run a real `hivemind.wardens.warden.Warden`
and stop cleanly on a `Shutdown` -- proving the wiring, not re-proving `link`'s own three-frame
sequencing (covered thoroughly by test_link.py) or the Warden's own tick shape (covered by
tests/unit/wardens/).

Fits into the Hive:
    Mirrors src/hivemind/cli/in_cell/main.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.in_cell.main for run_in_cell_warden, the module under test.
    - test_link.py for the three-frame sequence this module does not repeat.
"""

from __future__ import annotations

import asyncio

import pytest

from hivemind.cli.in_cell.main import run_in_cell_warden
from hivemind.common.errors import ConfigurationError
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.envelope import Hop, wrap
from waggle.ids import new_hive_id, new_node_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.messages.control.protocol import Shutdown
from waggle.messages.forage import CapacityReport
from waggle.messages.labels import Urgency
from waggle.signing import Ed25519Signer
from waggle.transport.websocket_server import WebSocketServer

WAIT_S = 5.0  # Bounds every await that could hang; loopback answers in milliseconds.
_CLOCK = SystemClock()
_CELL_ID = "cell_01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _environ(
    server_uri: str, queen_node_id: str, hive_id: str, queen_signer: Ed25519Signer
) -> dict[str, str]:
    """A real `HIVEMIND_*` environment naming `server_uri` as this Cell's own Queen."""
    cell_signer = Ed25519Signer.generate()
    return {
        "HIVEMIND_QUEEN_WAGGLE_URL": server_uri,
        "HIVEMIND_CELL_ID": _CELL_ID,
        "HIVEMIND_HIVE_ID": hive_id,
        "HIVEMIND_QUEEN_NODE_ID": queen_node_id,
        "HIVEMIND_CELL_SIGNING_KEY": cell_signer.private_key_bytes.hex(),
        "HIVEMIND_QUEEN_VERIFY_KEY": queen_signer.public_key_bytes.hex(),
    }


async def test_run_in_cell_warden_connects_announces_and_stops_on_shutdown() -> None:
    # Signed with queen_signer, the same key named as HIVEMIND_QUEEN_VERIFY_KEY below: the
    # in-Cell Warden's own transport carries a verifier (signing is mandatory across a machine
    # boundary, roadmap step 1.7) and refuses an unsigned frame outright, closing the connection
    # rather than ever reaching the Warden's own dispatch.
    queen_signer = Ed25519Signer.generate()
    server = WebSocketServer(Codec(signer=queen_signer))
    await server.start()
    try:
        hive_id = new_hive_id(_CLOCK)
        queen_node_id = new_node_id(_CLOCK)
        environ = _environ(server.uri, queen_node_id, hive_id, queen_signer)

        run_task = asyncio.ensure_future(run_in_cell_warden(environ, SystemClock()))
        connections = server.connections()
        server_transport = await asyncio.wait_for(anext(connections), timeout=WAIT_S)
        server_receive = server_transport.receive()

        # 1. CellReady first -- CellListener's own readiness gate accepts nothing else first.
        ready_envelope = await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)
        assert isinstance(ready_envelope.payload, CellReady)
        assert ready_envelope.payload.cell_id == _CELL_ID
        assert ready_envelope.signature is not None

        # 2. CapacityReport next, since CellReady carries no ForageCapacity.
        capacity_envelope = await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)
        assert isinstance(capacity_envelope.payload, CapacityReport)
        assert capacity_envelope.payload.cell_id == _CELL_ID

        # 3. One CellHeartbeat so the readiness gate resolves before a real Warden ever exists.
        heartbeat_envelope = await asyncio.wait_for(anext(server_receive), timeout=WAIT_S)
        assert isinstance(heartbeat_envelope.payload, CellHeartbeat)
        assert heartbeat_envelope.payload.cell_id == _CELL_ID

        # 4. A Queen-sent Shutdown stops the real Warden cleanly, ending run_in_cell_warden -- the
        # Warden's own supervision Heartbeat is on a much longer cadence (DEFAULT_HEARTBEAT_
        # INTERVAL_S) and is not worth a real-time wait here; test_link.py and tests/unit/wardens/
        # already cover it with a FakeClock.
        shutdown_hop = Hop(sender=hive_id, recipient=ready_envelope.sender, node_id=queen_node_id)
        shutdown = Shutdown(urgency=Urgency.IMMEDIATE, deadline_s=0.0, reason="test teardown")
        await server_transport.send(wrap(shutdown, shutdown_hop, clock=_CLOCK))

        await asyncio.wait_for(run_task, timeout=WAIT_S)
    finally:
        await server.close()


async def test_run_in_cell_warden_raises_on_missing_configuration() -> None:
    with pytest.raises(ConfigurationError):
        await run_in_cell_warden({}, SystemClock())
