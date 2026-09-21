"""Integration test: QemuCellBackend provisions a real VM against a loopback Queen.

`@pytest.mark.integration` (codingrules 14.2: "needs... real QEMU"). Skips cleanly, never errors,
when `qemu-img`/`qemu-system-x86_64` are not on `PATH` or `images/base-ubuntu/vm/base-ubuntu.qcow2`
has not been built locally -- this worktree's own dev host has no QEMU installed (ADR-0026), and
this module must still collect and be skippable there and in CI before a QEMU-enabled runner and
an image-build step exist. Mirrors `packages/hivemind/tests/integration/test_docker_backend.py`'s
own shape almost exactly, down to the `_LoopbackReadinessGate` test double, swapping
`DockerCellBackend`/`FakeDockerClient` for `QemuCellBackend`/`ProcessQemuRunner`.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises
    src/hivemind/hive/backends/qemu/backend.py end to end against a real `qemu-system-x86_64`.

Key invariants:
    - None: this module holds tests only.

See Also:
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md
    - docs/adr/0027-virtual-cells-connect-outbound-only-and-boot-a-warden.md
    - images/base-ubuntu/vm/README.md for how to build the image this test provisions.
    - packages/hivemind/tests/integration/test_docker_backend.py for the pattern this mirrors.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from builders.forage import make_capacity

from hivemind.cell import Cell, CellCapabilities, CellKind
from hivemind.hive.backends.bootstrap import CellReadyInfo, QueenEndpoint
from hivemind.hive.backends.qemu import (
    ProcessQemuRunner,
    QemuBackendConfig,
    QemuCellBackend,
)
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.ids import CellId, new_hive_id, new_node_id
from waggle.messages.cell.status import CellHeartbeat, CellReady
from waggle.transport.websocket_server import WebSocketServer

if TYPE_CHECKING:
    from waggle.transport.websocket import WebSocketTransport

pytestmark = pytest.mark.integration

_BASE_IMAGE = (
    Path(__file__).resolve().parents[3] / "images" / "base-ubuntu" / "vm" / "base-ubuntu.qcow2"
)
_WAIT_S = 180.0  # Real time: a cold boot plus cloud-init's own first-boot modules can take a while.
_ALL_INTERFACES = "0.0.0.0"  # noqa: S104  # SAFETY: a loopback-only bind is unreachable from a VM; this listener only ever exists for the life of this one test.


async def test_provision_real_vm_reaches_ready_then_destroy_removes_it(tmp_path: Path) -> None:
    """Provision base-ubuntu.qcow2 for real, wait for CellReady + Heartbeat, then destroy."""
    if shutil.which("qemu-img") is None or shutil.which("qemu-system-x86_64") is None:
        pytest.skip("qemu-img/qemu-system-x86_64 not on PATH")
    if not _BASE_IMAGE.is_file():
        pytest.skip(f"{_BASE_IMAGE} is not built locally; see images/base-ubuntu/vm/README.md")

    clock = SystemClock()
    server = WebSocketServer(Codec(), host=_ALL_INTERFACES)
    await server.start()
    gate = _LoopbackReadinessGate(server)
    with tempfile.TemporaryDirectory(prefix="hivemind-qemu-integration-") as vm_root:
        runner = ProcessQemuRunner(Path(vm_root))
        endpoint = QueenEndpoint(
            waggle_url=f"ws://10.0.2.2:{server.port}",  # QEMU user-net's own host alias.
            queen_node_id=new_node_id(clock),
            # Unverified by this test-only gate (see the module docstring): the real Queen-side
            # gate, a later roadmap step, is what actually checks a Cell's signature.
            queen_verify_key_hex="00" * 32,
        )
        config = QemuBackendConfig(base_image=_BASE_IMAGE, vm_root=Path(vm_root))
        backend = QemuCellBackend(runner, gate, endpoint, clock, config=config)
        spec = VirtualCellSpec(
            image="base-ubuntu",
            cpu_cores=1.0,
            memory_bytes=1024**3,
            disk_bytes=4 * 1024**3,
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
    QemuCellBackend's own sequencing against a real VM.
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
