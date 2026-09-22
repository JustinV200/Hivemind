"""Integration tests: Docker snapshot/rollback of a live container, and `hive cells` on a daemon.

`@pytest.mark.integration` (codingrules 14.2). Skips cleanly when no Docker daemon answers or
`images/base-ubuntu:dev` is not built (`integration.docker_helpers`). Two things the first
integration module leaves unproven: that `DockerSnapshotter` really commits and recreates a
running Cell (roadmap step 5.10) with the recreated container announcing itself again, and that
the operator-facing `hive cells inspect/abscond` commands (5.13) work against a real daemon from
backend labels alone, with no Queen running.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises hive/snapshot/docker.py and
    cli/readback/virtual*.py end to end against a real daemon.

Key invariants:
    - None: this module holds tests only.

See Also:
    - packages/hivemind/tests/integration/test_docker_backend.py for the provisioning half.
    - docs/adr/0026-cell-backends-docker-first-qemu-second.md for "labels are the source of
      truth for orphans", the property abscond relies on here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.forage import make_capacity
from integration.docker_helpers import (
    ALL_INTERFACES,
    IMAGE,
    WAIT_S,
    LoopbackReadinessGate,
    daemon_reachable,
    image_present,
)
from typer.testing import CliRunner

from hivemind.cell import Cell
from hivemind.cli.app import app
from hivemind.hive.backends.base import VirtualCellRecord
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.docker import DockerCellBackend, SdkDockerClient
from hivemind.hive.models import NetworkPolicy, VirtualCellSpec
from hivemind.hive.snapshot import DockerSnapshotter, SnapshotLedger
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.ids import HiveId, new_hive_id, new_node_id
from waggle.transport.websocket_server import WebSocketServer

pytestmark = pytest.mark.integration
runner = CliRunner()


def _skip_unless_docker() -> None:
    if not daemon_reachable():
        pytest.skip("no Docker daemon reachable")
    if not image_present(IMAGE):
        pytest.skip(f"{IMAGE} is not built locally; see images/base-ubuntu/README.md")


async def test_snapshot_commits_and_rollback_recreates_a_live_container() -> None:
    """Snapshot a running Cell (docker commit), roll it back (recreate), see it announce again."""
    _skip_unless_docker()
    async with _Daemon() as daemon:
        ledger = SnapshotLedger()
        snapshotter = DockerSnapshotter(daemon.client, ledger, daemon.clock)
        cell = await daemon.backend.provision(_spec(daemon.clock, new_hive_id(daemon.clock)))
        try:
            snapshot_id = await snapshotter.snapshot(cell)
            assert ledger.get(snapshot_id).cell_id == cell.id

            # Rollback recreates the container from the committed image; the fresh container
            # boots the same entry point with the same env, so it dials back and announces again.
            await daemon.gate.forget(cell.id)
            await daemon.gate.expect(cell.id, "00" * 32)
            await snapshotter.rollback(cell, snapshot_id)
            await daemon.gate.wait_ready(cell.id, WAIT_S)
        finally:
            await daemon.backend.destroy(cell.id)
            for image in _committed_images(snapshotter):
                await daemon.client.remove_image(image)


def test_cli_inspect_and_abscond_work_from_labels_with_no_queen(tmp_path: Path) -> None:
    """`hive cells inspect` sees a real container; `abscond --yes` leaves the daemon empty."""
    _skip_unless_docker()
    manifest_path = _docker_manifest(tmp_path)
    hive_id = load_manifest(manifest_path, {}).hive.id
    cell = asyncio.run(_provision_and_detach(hive_id))
    try:
        result = runner.invoke(app, ["cells", "inspect", cell.id, "--manifest", str(manifest_path)])
        assert result.exit_code == 0, result.output
        assert "docker" in result.output
        assert IMAGE in result.output

        result = runner.invoke(app, ["cells", "abscond", "--manifest", str(manifest_path), "--yes"])
        assert result.exit_code == 0, result.output
        assert "containers_destroyed:      1" in result.output
    finally:
        asyncio.run(_destroy_every(hive_id))
    assert asyncio.run(_list(hive_id)) == ()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


class _Daemon:
    """A loopback Waggle listener, its readiness gate and a real DockerCellBackend, as one unit."""

    def __init__(self) -> None:
        self.clock = SystemClock()
        self.client = SdkDockerClient()

    async def __aenter__(self) -> _Daemon:
        self.server = WebSocketServer(Codec(), host=ALL_INTERFACES)
        await self.server.start()
        self.gate = LoopbackReadinessGate(self.server)
        endpoint = QueenEndpoint(
            waggle_url=f"ws://host.docker.internal:{self.server.port}",
            queen_node_id=new_node_id(self.clock),
            queen_verify_key_hex="00" * 32,  # Unverified by the loopback gate (docker_helpers).
        )
        self.backend = DockerCellBackend(self.client, self.gate, endpoint, self.clock)
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.gate.close()
        await self.server.close()


def _spec(clock: SystemClock, hive_id: HiveId) -> VirtualCellSpec:
    return VirtualCellSpec(
        image=IMAGE,
        cpu_cores=1.0,
        memory_bytes=512 * 1024**2,
        disk_bytes=1024**3,
        capacity=make_capacity(),
        hive_id=hive_id,
        network_policy=NetworkPolicy.EGRESS_ONLY,
        ready_timeout_s=WAIT_S,
    )


def _committed_images(snapshotter: DockerSnapshotter) -> list[str]:
    """Every committed image this snapshotter still knows, for cleanup."""
    return list(snapshotter._images.values())  # Cleanup of a private map.


def _docker_manifest(tmp_path: Path) -> Path:
    """A fake_manifest extended with a Docker-backed [virtual_cells] section."""
    path = fake_manifest(tmp_path)
    section = (
        "\n[virtual_cells]\n"
        'backend = "docker"\n'
        f'default_image = "{IMAGE}"\n'
        'listen_host = "0.0.0.0"\n'
    )
    path.write_text(path.read_text(encoding="utf-8") + section, encoding="utf-8")
    return path


async def _provision_and_detach(hive_id: HiveId) -> Cell:
    """Provision one real Cell for `hive_id`, then drop the gate and server: the CLI runs alone."""
    async with _Daemon() as daemon:
        return await daemon.backend.provision(_spec(daemon.clock, hive_id))


async def _list(hive_id: HiveId) -> Sequence[VirtualCellRecord]:
    async with _Daemon() as daemon:
        return await daemon.backend.list_cells(hive_id)


async def _destroy_every(hive_id: HiveId) -> None:
    """Belt and braces: destroy anything a failed assertion left labelled with `hive_id`."""
    async with _Daemon() as daemon:
        for record in await daemon.backend.list_cells(hive_id):
            await daemon.backend.destroy(record.cell_id)
