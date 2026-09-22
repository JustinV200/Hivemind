"""Unit tests for hivemind.hive.snapshot.docker: DockerSnapshotter over FakeDockerClient.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/snapshot/docker.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.snapshot.docker for DockerSnapshotter, the class under test.
    - packages/hivemind/tests/contracts/test_snapshotter_contract.py for the shared contract
      suite this module's own fixture also feeds.
"""

from __future__ import annotations

import pytest
from builders.cells import make_cell

from hivemind.cell import Cell, CellKind, SnapshotId
from hivemind.hive.backends.docker.backend import container_name
from hivemind.hive.backends.docker.client import ContainerSpec
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.snapshot.docker import DockerSnapshotter
from hivemind.hive.snapshot.ledger import SnapshotLedger, SnapshotNotFoundError
from waggle.clock import FakeClock


def _virtual_cell(clock: FakeClock) -> Cell:
    return make_cell(kind=CellKind.VIRTUAL, clock=clock)


async def test_snapshot_commits_the_cells_own_container() -> None:
    clock = FakeClock()
    client = FakeDockerClient()
    cell = _virtual_cell(clock)
    await client.create_container(_container_spec(cell))
    snapshotter = DockerSnapshotter(client, SnapshotLedger(), clock)

    await snapshotter.snapshot(cell)

    assert client.commit_container_calls == [container_name(cell.id)]


async def test_snapshot_then_rollback_recreates_from_the_matching_committed_image() -> None:
    clock = FakeClock()
    client = FakeDockerClient()
    cell = _virtual_cell(clock)
    await client.create_container(_container_spec(cell))
    snapshotter = DockerSnapshotter(client, SnapshotLedger(), clock)

    first_id = await snapshotter.snapshot(cell)
    second_id = await snapshotter.snapshot(cell)  # A second, distinct commit.
    await snapshotter.rollback(cell, first_id)

    assert first_id != second_id
    assert len(client.recreate_from_image_calls) == 1
    recreated_name, recreated_image = client.recreate_from_image_calls[0]
    assert recreated_name == container_name(cell.id)
    # The image rollback recreated from names the *first* snapshot's own image (the id it was
    # asked to restore), proving rollback maps each SnapshotId to its own committed image rather
    # than always reusing whichever commit happened most recently.
    assert recreated_image.endswith(str(first_id).removeprefix("snap_docker_"))


async def test_rollback_of_an_unknown_snapshot_id_raises() -> None:
    clock = FakeClock()
    snapshotter = DockerSnapshotter(FakeDockerClient(), SnapshotLedger(), clock)
    cell = _virtual_cell(clock)

    with pytest.raises(SnapshotNotFoundError):
        await snapshotter.rollback(cell, SnapshotId("snap_docker_never-taken"))


async def test_snapshot_records_disk_usage_on_the_shared_ledger() -> None:
    clock = FakeClock()
    client = FakeDockerClient()
    client.set_commit_size_bytes(4096)
    cell = _virtual_cell(clock)
    await client.create_container(_container_spec(cell))
    ledger = SnapshotLedger()
    snapshotter = DockerSnapshotter(client, ledger, clock)

    await snapshotter.snapshot(cell)

    assert ledger.disk_used_bytes(cell.id) == 4096


async def test_snapshot_over_budget_evicts_the_oldest_snapshot_first() -> None:
    clock = FakeClock()
    client = FakeDockerClient()
    client.set_commit_size_bytes(600)
    cell = _virtual_cell(clock)
    await client.create_container(_container_spec(cell))
    ledger = SnapshotLedger()
    snapshotter = DockerSnapshotter(client, ledger, clock, disk_budget_bytes=1000)

    first_id = await snapshotter.snapshot(cell)
    clock.advance(1.0)
    second_id = await snapshotter.snapshot(cell)  # 600 + 600 > 1000: evicts `first_id`.

    assert client.remove_image_calls  # The evicted snapshot's own image was cleaned up too.
    with pytest.raises(SnapshotNotFoundError):
        ledger.get(first_id)
    assert ledger.get(second_id).cell_id == cell.id
    assert ledger.disk_used_bytes(cell.id) == 600


def _container_spec(cell: Cell) -> ContainerSpec:
    """Build a minimal ContainerSpec so FakeDockerClient's own container table has an entry."""
    return ContainerSpec(
        name=container_name(cell.id),
        image="base-ubuntu",
        environment={},
        labels={},
        network_name="test-network",
        extra_hosts={},
        volume_name=None,
        volume_mount_path="/var/lib/hivemind/scratch",
        nano_cpus=1_000_000_000,
        mem_limit_bytes=1024**3,
        pids_limit=256,
        cap_drop=("ALL",),
        security_opt=("no-new-privileges:true",),
        read_only_rootfs=True,
    )
