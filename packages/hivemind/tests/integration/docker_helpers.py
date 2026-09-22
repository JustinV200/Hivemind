"""Shared helpers for the Docker integration tests: a loopback readiness gate and skip probes.

`LoopbackReadinessGate` is a small, test-only `ReadinessGate`: it reads real `CellReady` and
`CellHeartbeat` frames off a real `waggle.transport.websocket_server.WebSocketServer` and
correlates them by `cell_id`. It is not shipped: it exists only to prove the Docker backend's own
provisioning sequence end to end against a real container, without the real Queen-side gate.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Used by every module under tests/integration
    that provisions a real container.

Key invariants:
    - None: this module holds test helpers only.

See Also:
    - packages/hivemind/tests/integration/test_docker_backend.py for the first user.
"""

from __future__ import annotations

import asyncio

from builders.forage import make_capacity

from hivemind.cell import CellCapabilities
from hivemind.hive.backends.bootstrap import CellReadyInfo
from waggle.ids import CellId
from waggle.messages.cell import CellHeartbeat, CellReady
from waggle.transport.websocket import WebSocketTransport
from waggle.transport.websocket_server import WebSocketServer

IMAGE = "hivemind/base-ubuntu:dev"  # Matches images/base-ubuntu/README.md's own build command.
WAIT_S = 90.0  # Real time: a cold container start plus a Python boot can take a while.
ALL_INTERFACES = "0.0.0.0"  # noqa: S104  # SAFETY: a loopback-only bind is unreachable from a container; the listener only lives for one test.

__all__ = [
    "ALL_INTERFACES",
    "IMAGE",
    "WAIT_S",
    "LoopbackReadinessGate",
    "daemon_reachable",
    "image_present",
]


class LoopbackReadinessGate:
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


def daemon_reachable() -> bool:
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


def image_present(tag: str) -> bool:
    """Return whether `tag` exists in the local Docker image store."""
    import docker
    from docker.errors import ImageNotFound

    try:
        docker.from_env().images.get(tag)
    except ImageNotFound:
        return False
    return True
