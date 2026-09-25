"""Unit tests for hivemind.hive.snapshot.factory: snapshotter_for, chosen by capability alone.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Mirrors
    src/hivemind/hive/snapshot/factory.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.snapshot.factory for snapshotter_for, the function under test.
"""

from __future__ import annotations

from pathlib import Path

from hivemind.cell import NoopSnapshotter
from hivemind.hive.backends.base import BackendCapabilities
from hivemind.hive.backends.bootstrap import QueenEndpoint
from hivemind.hive.backends.docker.backend import DockerCellBackend
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.backends.fake import FakeCellBackend, FakeReadinessGate
from hivemind.hive.backends.qemu.backend import QemuBackendConfig, QemuCellBackend
from hivemind.hive.backends.qemu.fake import FakeQemuRunner
from hivemind.hive.snapshot.docker import DockerSnapshotImages, DockerSnapshotter
from hivemind.hive.snapshot.factory import snapshot_images_for, snapshotter_for
from hivemind.hive.snapshot.ledger import SnapshotLedger
from hivemind.hive.snapshot.qemu import QemuSnapshotter
from waggle.clock import FakeClock
from waggle.ids import new_node_id


def _endpoint(clock: FakeClock) -> QueenEndpoint:
    """Build a valid QueenEndpoint, matching the shape every backend test in this repo uses."""
    return QueenEndpoint(
        waggle_url="ws://localhost:8710",
        queen_node_id=new_node_id(clock),
        queen_verify_key_hex="00" * 32,
    )


def _docker_backend(clock: FakeClock) -> DockerCellBackend:
    return DockerCellBackend(FakeDockerClient(), FakeReadinessGate(clock), _endpoint(clock), clock)


def _qemu_backend(clock: FakeClock, tmp_path: Path) -> QemuCellBackend:
    config = QemuBackendConfig(base_image=tmp_path / "base.qcow2", vm_root=tmp_path / "vms")
    return QemuCellBackend(
        FakeQemuRunner(), FakeReadinessGate(clock), _endpoint(clock), clock, config=config
    )


async def test_snapshotter_for_a_docker_backend_returns_a_docker_snapshotter() -> None:
    clock = FakeClock()
    backend = _docker_backend(clock)

    snapshotter = snapshotter_for(backend, SnapshotLedger(), clock)

    assert isinstance(snapshotter, DockerSnapshotter)


async def test_snapshotter_for_a_qemu_backend_returns_a_qemu_snapshotter(tmp_path: Path) -> None:
    clock = FakeClock()
    backend = _qemu_backend(clock, tmp_path)

    snapshotter = snapshotter_for(backend, SnapshotLedger(), clock)

    assert isinstance(snapshotter, QemuSnapshotter)


async def test_snapshotter_for_a_backend_that_cannot_snapshot_returns_noop() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(
        clock, BackendCapabilities(can_snapshot=False, can_pause=True, headroom=None)
    )

    snapshotter = snapshotter_for(backend, SnapshotLedger(), clock)

    assert isinstance(snapshotter, NoopSnapshotter)


async def test_snapshotter_for_a_backend_with_no_registered_builder_degrades_to_noop() -> None:
    """FakeCellBackend claims can_snapshot=True but this factory has no builder for its type."""
    clock = FakeClock()
    backend = FakeCellBackend(
        clock, BackendCapabilities(can_snapshot=True, can_pause=True, headroom=None)
    )

    snapshotter = snapshotter_for(backend, SnapshotLedger(), clock)

    assert isinstance(snapshotter, NoopSnapshotter)


async def test_only_a_docker_backend_has_snapshot_images_to_purge(tmp_path: Path) -> None:
    clock = FakeClock()
    snapshotting_fake = FakeCellBackend(
        clock, BackendCapabilities(can_snapshot=True, can_pause=True, headroom=None)
    )

    assert isinstance(snapshot_images_for(_docker_backend(clock)), DockerSnapshotImages)
    # A QEMU snapshot lives in the Cell's own overlay, removed with its VM directory.
    assert snapshot_images_for(_qemu_backend(clock, tmp_path)) is None
    assert snapshot_images_for(snapshotting_fake) is None
