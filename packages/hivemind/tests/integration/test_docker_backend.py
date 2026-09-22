"""Integration test: DockerCellBackend provisions a real container against a loopback Queen.

`@pytest.mark.integration` (codingrules 14.2: "needs... Docker"). Skips cleanly, never errors,
when no Docker daemon answers (`docker.from_env().ping()`) or `images/base-ubuntu:dev` has not
been built locally -- this worktree's own dev host has no Docker installed (ADR-0026), and this
module must still collect and be skippable there and in CI before a Docker-enabled runner and an
image-build step exist (see this file's own final comment for what `.github/workflows/
integration.yml` still needs).

`LoopbackReadinessGate` is a small, test-only `ReadinessGate`: it reads real `CellReady` and
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

from typing import TYPE_CHECKING

import pytest
from builders.forage import make_capacity
from integration.docker_helpers import (
    ALL_INTERFACES,
    IMAGE,
    WAIT_S,
    LoopbackReadinessGate,
    daemon_reachable,
    image_present,
)

from hivemind.cell import Cell, CellKind
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.docker import DockerCellBackend, SdkDockerClient
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.ids import new_hive_id, new_node_id
from waggle.transport.websocket_server import WebSocketServer

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.integration


async def test_provision_real_container_reaches_ready_then_destroy_removes_it() -> None:
    """Provision images/base-ubuntu:dev for real, wait for CellReady + Heartbeat, then destroy."""
    if not daemon_reachable():
        pytest.skip("no Docker daemon reachable")
    if not image_present(IMAGE):
        pytest.skip(f"{IMAGE} is not built locally; see images/base-ubuntu/README.md")

    clock = SystemClock()
    server = WebSocketServer(Codec(), host=ALL_INTERFACES)
    await server.start()
    gate = LoopbackReadinessGate(server)
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
        image=IMAGE,
        cpu_cores=1.0,
        memory_bytes=512 * 1024**2,
        disk_bytes=1024**3,
        capacity=make_capacity(),
        hive_id=new_hive_id(clock),
        network_policy=NetworkPolicy.EGRESS_ONLY,
        ready_timeout_s=WAIT_S,
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
