"""Integration test: DockerCellBackend provisions a real container against a loopback Queen.

`@pytest.mark.integration` (codingrules 14.2: "needs... Docker"). Skips cleanly, never errors,
when no Docker daemon answers (`docker.from_env().ping()`) or `images/base-ubuntu:dev` has not
been built locally -- this worktree's own dev host has no Docker installed (ADR-0026), and this
module must still collect and be skippable there and in CI before a Docker-enabled runner and an
image-build step exist (see this file's own final comment for what `.github/workflows/
integration.yml` still needs).

`_LoopbackReadinessGate` is a small, test-only `ReadinessGate`: it reads real `CellReady` and
`CellHeartbeat` frames off a real `waggle.transport.websocket_server.WebSocketServer` and
correlates them by `cell_id` (carried directly on both message bodies, so no `node_id` resolution
is needed here -- that machinery belongs to the real, Queen-side gate a later roadmap step
builds). It is not shipped: it exists only to prove `DockerCellBackend`'s provisioning sequence
end to end against a real container, the same way `packages/hivemind/tests/unit/cli/in_cell/
test_main.py` proves the in-Cell entry point's own wiring with a `WebSocketServer` test double
instead of a container.

NOTE on a real gap this test surfaces, not one it introduces: the Cell's own `HIVEMIND_QUEEN_
WAGGLE_URL` here is `ws://host.docker.internal:<port>` -- exactly what `hivemind.hive.backends.
docker.network`'s host-gateway wiring is for (ADR-0027's "the Queen needs a reachable listener").
But `waggle.uris.check_waggle_uri`, which the in-Cell entry point's own WebSocketClientTransport
calls before dialling, accepts `ws://` only on a loopback host (`localhost` or 127.0.0.0/8): from
inside the container, `host.docker.internal` is neither, so the Cell's own connect will raise
`ValueError` and this test will time out waiting for `CellReady` on a real Docker host, not
because DockerCellBackend or this test is wrong, but because nothing has yet decided whether the
Docker host-gateway address gets a `check_waggle_uri` carve-out, or Docker deployments are
expected to front the Queen's listener with `wss://` instead (ADR-0026/0027 name the problem;
neither settles it). Flagged in this dispatch's own report rather than worked around here.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises
    src/hivemind/hive/backends/docker/backend.py end to end against a real daemon.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md
    - images/base-ubuntu/README.md "Building and testing this image" for what a Docker-enabled CI
      runner must do with this image before this test can provision it for real.
    - packages/hivemind/tests/unit/cli/in_cell/test_main.py for the WebSocketServer test-double
      pattern this module reuses.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest
from builders.forage import make_capacity

from hivemind.cell import Cell, CellCapabilities, CellKind
from hivemind.hive.backends.bootstrap import CellReadyInfo, QueenEndpoint
from hivemind.hive.backends.docker import DockerCellBackend, SdkDockerClient
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.ids import CellId, new_hive_id, new_node_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.transport.websocket_server import WebSocketServer

if TYPE_CHECKING:
    from waggle.transport.websocket import WebSocketTransport

pytestmark = pytest.mark.integration

_IMAGE = "hivemind/base-ubuntu:dev"  # Matches images/base-ubuntu/README.md's own build command.
_WAIT_S = 90.0  # Real time: a cold `docker pull`/start plus a Python boot can take a while.
_ALL_INTERFACES = "0.0.0.0"  # noqa: S104  # SAFETY: a loopback-only bind is unreachable from a container; this listener only ever exists for the life of this one test.


async def test_provision_real_container_reaches_ready_then_destroy_removes_it() -> None:
    """Provision images/base-ubuntu:dev for real, wait for CellReady + Heartbeat, then destroy."""
    if not _daemon_reachable():
        pytest.skip("no Docker daemon reachable")
    if not _image_present(_IMAGE):
        pytest.skip(f"{_IMAGE} is not built locally; see images/base-ubuntu/README.md")

    clock = SystemClock()
    server = WebSocketServer(Codec(), host=_ALL_INTERFACES)
    await server.start()
    gate = _LoopbackReadinessGate(server)
    client = SdkDockerClient()
    endpoint = QueenEndpoint(
        waggle_url=f"ws://host.docker.internal:{server.port}",
        queen_node_id=new_node_id(clock),
        # Unverified by this test-only gate (see the module docstring): the real Queen-side gate,
        # a later roadmap step, is what actually checks a Cell's signature against this key.
        queen_verify_key_hex="00" * 32,
    )
    backend = DockerCellBackend(client, gate, endpoint, clock)
    spec = VirtualCellSpec(
        image=_IMAGE,
        cpu_cores=1.0,
        memory_bytes=512 * 1024**2,
        disk_bytes=1024**3,
        capacity=make_capacity(),
        hive_id=new_hive_id(clock),
        network_policy=NetworkPolicy.EGRESS_ONLY,
        ready_timeout_s=_WAIT_S,
    )

    cell: Cell | None = None
    try:
        cell = await backend.provision(spec)
        assert cell.kind is CellKind.VIRTUAL
        assert cell.capabilities.os.value == "LINUX"
    finally:
        if cell is not None:
            await backend.destroy(cell.id)
        await gate.close()
        await server.close()


class _LoopbackReadinessGate:
    """Test-only ReadinessGate: correlates real CellReady/CellHeartbeat frames by cell_id.

    See the module docstring: not the real Queen-side gate, just enough to prove
    DockerCellBackend's own sequencing against a real container.
    """

    def __init__(self, server: WebSocketServer) -> None:
        """Start listening for connections on `server` immediately; nothing is expected yet."""
        self._ready: dict[CellId, CellReadyInfo] = {}
        self._became_ready: dict[CellId, asyncio.Event] = {}
        self._reader_tasks: list[asyncio.Task[None]] = []  # Keeps every _read() task referenced.
        self._listen_task = asyncio.create_task(self._listen(server))

    async def expect(self, cell_id: CellId, verify_key_hex: str) -> None:
        """Register `cell_id`; see `ReadinessGate.expect`."""
        self._became_ready[cell_id] = asyncio.Event()

    async def wait_ready(self, cell_id: CellId, timeout_s: float) -> CellReadyInfo:
        """Block until this Cell's CellReady and first CellHeartbeat both arrived."""
        event = self._became_ready[cell_id]
        await asyncio.wait_for(event.wait(), timeout=timeout_s)
        return self._ready[cell_id]

    async def forget(self, cell_id: CellId) -> None:
        """Drop any state for `cell_id`; see `ReadinessGate.forget` (idempotent)."""
        self._became_ready.pop(cell_id, None)
        self._ready.pop(cell_id, None)

    async def close(self) -> None:
        """Stop this gate's own background listener and reader tasks."""
        self._listen_task.cancel()
        for task in self._reader_tasks:
            task.cancel()

    async def _listen(self, server: WebSocketServer) -> None:
        """Hand every accepted connection to its own reader task, for the test's whole life."""
        async for transport in server.connections():
            self._reader_tasks.append(asyncio.create_task(self._read(transport)))

    async def _read(self, transport: WebSocketTransport) -> None:
        """Read one connection's frames: record CellReady, then its first matching CellHeartbeat."""
        async for envelope in transport.receive():
            payload = envelope.payload
            if isinstance(payload, CellReady):
                cell_id = CellId(payload.cell_id)
                self._ready[cell_id] = CellReadyInfo(
                    capabilities=CellCapabilities.from_wire(payload.platform, payload.capabilities),
                    # base-ubuntu's current Warden entry point sends no forage.capacity_report yet
                    # (roadmap step 5.5's own "not yet built" list): a placeholder stands in until
                    # a later step wires real capacity reporting through this same handshake.
                    capacity=make_capacity(),
                )
            elif isinstance(payload, CellHeartbeat):
                cell_id = CellId(payload.cell_id)
                event = self._became_ready.get(cell_id)
                if cell_id in self._ready and event is not None:
                    event.set()


def _daemon_reachable() -> bool:
    """Return whether a Docker daemon answers; never raises, since that should mean "skip"."""
    try:
        import docker
    except ImportError:
        return False
    try:
        docker.from_env().ping()
    except Exception:  # SAFETY: any failure here means "skip this test", never "error the run".
        return False
    return True


def _image_present(tag: str) -> bool:
    """Return whether `tag` exists in the local Docker image store."""
    import docker
    from docker.errors import ImageNotFound

    try:
        docker.from_env().images.get(tag)
    except ImageNotFound:
        return False
    return True
