"""Contract suite for Snapshotter: one contract, run over DockerSnapshotter and QemuSnapshotter.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.cell.snapshot.Snapshotter contract and runs against every harness in `_HARNESSES`
    below (roadmap step 5.10): `hivemind.hive.snapshot.docker.DockerSnapshotter` over
    `hivemind.hive.backends.docker.fake.FakeDockerClient`, and `hivemind.hive.snapshot.qemu.
    QemuSnapshotter` over `hivemind.hive.backends.qemu.fake.FakeQemuRunner`. The "unsupported
    backend falls back to REVERSE_DIFF" clause (ADR-0018) is proven separately, against
    `hivemind.cell.NoopSnapshotter` directly, since it is a property of that one implementation
    rather than something either backend-specific harness here could exercise meaningfully.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.snapshot for the Snapshotter protocol under test.
    - hivemind.hive.snapshot.ledger for SnapshotLedger, exercised for retention and budget.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for the SnapshotUnsupportedError
      contract the gate's own REVERSE_DIFF fallback depends on.
"""

from __future__ import annotations

from typing import Protocol

import pytest
from builders.cells import make_cell

from hivemind.cell import (
    Cell,
    CellKind,
    NoopSnapshotter,
    SnapshotId,
    Snapshotter,
    SnapshotUnsupportedError,
)
from hivemind.hive.backends.docker.backend import container_name
from hivemind.hive.backends.docker.client import ContainerSpec
from hivemind.hive.backends.docker.fake import FakeDockerClient
from hivemind.hive.backends.qemu.fake import FakeQemuRunner
from hivemind.hive.snapshot.docker import DockerSnapshotter
from hivemind.hive.snapshot.ledger import SnapshotLedger, SnapshotNotFoundError
from hivemind.hive.snapshot.qemu import QemuSnapshotter
from waggle.clock import Clock, FakeClock


class SnapshotterHarness(Protocol):
    """Build a Snapshotter of one kind, over a Cell already known to its own fake backend."""

    async def build(
        self, clock: Clock, *, retention_s: float = 3600.0, disk_budget_bytes: int | None = None
    ) -> tuple[Snapshotter, Cell, SnapshotLedger]:
        """Return a fresh Snapshotter, the VIRTUAL Cell it already knows about, and its ledger."""
        ...

    def set_next_snapshot_size_bytes(self, size: int) -> None:
        """Arrange the size every following `snapshot()` call reports for disk-Forage accounting."""
        ...


class _DockerHarness:
    """Builds a DockerSnapshotter over a FakeDockerClient with one container already created."""

    def __init__(self) -> None:
        self._client = FakeDockerClient()

    async def build(
        self, clock: Clock, *, retention_s: float = 3600.0, disk_budget_bytes: int | None = None
    ) -> tuple[Snapshotter, Cell, SnapshotLedger]:
        self._client = FakeDockerClient()
        cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
        await self._client.create_container(_container_spec(cell))
        ledger = SnapshotLedger()
        snapshotter = DockerSnapshotter(
            self._client,
            ledger,
            clock,
            retention_s=retention_s,
            disk_budget_bytes=disk_budget_bytes,
        )
        return snapshotter, cell, ledger

    def set_next_snapshot_size_bytes(self, size: int) -> None:
        self._client.set_commit_size_bytes(size)


class _QemuHarness:
    """Builds a QemuSnapshotter over a fresh FakeQemuRunner."""

    def __init__(self) -> None:
        self._runner = FakeQemuRunner()

    async def build(
        self, clock: Clock, *, retention_s: float = 3600.0, disk_budget_bytes: int | None = None
    ) -> tuple[Snapshotter, Cell, SnapshotLedger]:
        self._runner = FakeQemuRunner()
        cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
        ledger = SnapshotLedger()
        snapshotter = QemuSnapshotter(
            self._runner,
            ledger,
            clock,
            retention_s=retention_s,
            disk_budget_bytes=disk_budget_bytes,
        )
        return snapshotter, cell, ledger

    def set_next_snapshot_size_bytes(self, size: int) -> None:
        self._runner.set_savevm_size_bytes(size)


_HARNESSES: dict[str, SnapshotterHarness] = {"docker": _DockerHarness(), "qemu": _QemuHarness()}


@pytest.fixture(params=sorted(_HARNESSES))
def harness(request: pytest.FixtureRequest) -> SnapshotterHarness:
    """One SnapshotterHarness per registered backend-specific implementation."""
    return _HARNESSES[request.param]


async def test_snapshot_then_rollback_does_not_raise(harness: SnapshotterHarness) -> None:
    """The basic round trip: a snapshot taken can always be rolled back to."""
    clock = FakeClock()
    snapshotter, cell, _ledger = await harness.build(clock)

    snapshot_id = await snapshotter.snapshot(cell)
    await snapshotter.rollback(cell, snapshot_id)  # Must not raise.


async def test_rollback_of_an_unknown_id_raises_snapshot_not_found(
    harness: SnapshotterHarness,
) -> None:
    clock = FakeClock()
    snapshotter, cell, _ledger = await harness.build(clock)

    with pytest.raises(SnapshotNotFoundError):
        await snapshotter.rollback(cell, SnapshotId("snap_contract_never_taken"))


async def test_retention_expiry_removes_the_snapshot_from_the_ledger(
    harness: SnapshotterHarness,
) -> None:
    clock = FakeClock()
    snapshotter, cell, ledger = await harness.build(clock, retention_s=60.0)

    snapshot_id = await snapshotter.snapshot(cell)
    clock.advance(120.0)  # Past the 60s retention window.
    expired = ledger.expire(clock.now())

    assert snapshot_id in expired
    with pytest.raises(SnapshotNotFoundError):
        ledger.get(snapshot_id)


async def test_budget_eviction_removes_the_oldest_snapshot_for_the_same_cell(
    harness: SnapshotterHarness,
) -> None:
    clock = FakeClock()
    snapshotter, cell, ledger = await harness.build(clock, disk_budget_bytes=1000)
    harness.set_next_snapshot_size_bytes(600)

    first_id = await snapshotter.snapshot(cell)
    clock.advance(1.0)
    second_id = await snapshotter.snapshot(cell)  # 600 + 600 > 1000: evicts `first_id`.

    with pytest.raises(SnapshotNotFoundError):
        ledger.get(first_id)
    assert ledger.get(second_id).cell_id == cell.id


# ──────────────────────────────────────────────────────────────────────────────
# NoopSnapshotter's own clause: a backend without can_snapshot falls back to REVERSE_DIFF (ADR-0018)
# ──────────────────────────────────────────────────────────────────────────────


async def test_unsupported_backends_snapshotter_raises_snapshot_unsupported_on_rollback() -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    snapshotter: Snapshotter = NoopSnapshotter()

    snapshot_id = await snapshotter.snapshot(cell)  # Never raises: nothing was captured.

    with pytest.raises(SnapshotUnsupportedError):
        await snapshotter.rollback(cell, snapshot_id)


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
