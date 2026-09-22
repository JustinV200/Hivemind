"""Tests for hivemind.workers.roles.undertaker.sweep: sweep_orphans and its pure decision halves.

Fits into the Hive:
    Mirrors src/hivemind/workers/roles/undertaker/sweep.py (codingrules section 3: tests/unit
    mirrors src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.workers.roles.undertaker.sweep for the module under test.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from pathlib import Path

from builders.cells import make_identity, make_real_cell_lease
from unit.workers.roles.undertaker.fakes import (
    RecordingGrantRevoker,
    RecordingLeavingsRemover,
    RecordingWaxRetirer,
)

from hivemind.cell import CellIdentity, RealCellLease
from hivemind.cell.fake import FakeLeaseReleaser
from hivemind.forage import ForageCapacity, HostCapacity
from hivemind.hive.backends.base import VirtualCellRecord
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.cell_state import VirtualCellStatus
from hivemind.hive.models import VirtualCellSpec
from hivemind.pheromone import CellEvent, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.workers.roles.undertaker.role import Undertaker, UndertakerDeps
from hivemind.workers.roles.undertaker.sweep import (
    SweepDeps,
    orphan_real_leases,
    orphan_virtual_cells,
    sweep_orphans,
)
from waggle.clock import FakeClock
from waggle.ids import (
    CellId,
    HiveId,
    LeaseId,
    TaskId,
    new_cell_id,
    new_event_id,
    new_lease_id,
    new_task_id,
)
from waggle.messages import OsFamily as WireOsFamily


def _record(clock: FakeClock, cell_id: CellId, image: str = "base-ubuntu") -> VirtualCellRecord:
    """Build a VirtualCellRecord for `cell_id`, as `CellBackend.list_cells` would report it."""
    return VirtualCellRecord(
        cell_id=cell_id,
        status=VirtualCellStatus.READY,
        image=image,
        labels={},
        created_at=clock.now(),
    )


def _leased_event(clock: FakeClock, lease_id: LeaseId, task_id: TaskId | None) -> CellEvent:
    """Build a cell.leased CellEvent carrying `lease_id`/`task_id` in its payload."""
    identity = make_identity(clock=clock)
    return CellEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind="cell.leased",
        subject_id=new_cell_id(clock),
        payload={"lease_id": lease_id, "task_id": task_id, "access_level": "SCRATCH"},
    )


def _released_event(clock: FakeClock, lease_id: LeaseId) -> CellEvent:
    """Build a cell.released CellEvent carrying `lease_id` in its payload."""
    identity = make_identity(clock=clock)
    return CellEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind="cell.released",
        subject_id=new_cell_id(clock),
        payload={"lease_id": lease_id, "access_level": "SCRATCH"},
    )


def _make_spec(hive_id: HiveId) -> VirtualCellSpec:
    """Build a minimal, valid VirtualCellSpec for one FakeCellBackend.provision() call.

    Args:
        hive_id: Must equal the `SweepDeps.hive_id` the test's own sweep will query by, or the
            provisioned Cell's label never matches and `backend.list_cells` never finds it.
    """
    capacity = ForageCapacity(
        host=HostCapacity(
            cores=1,
            memory_bytes=1,
            memory_free_bytes=1,
            disk_bytes=1,
            disk_free_bytes=1,
            cpu_load=0.0,
            gpus=(),
            arch="x86_64",
            os=WireOsFamily.LINUX,
        ),
        local_seats=(),
        max_sub_bees=1,
    )
    return VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1,
        disk_bytes=1,
        capacity=capacity,
        hive_id=hive_id,
    )


# ──────────────────────────────────────────────────────────────────────────────
# orphan_virtual_cells (pure)
# ──────────────────────────────────────────────────────────────────────────────


def test_orphan_virtual_cells_excludes_known_live_ids() -> None:
    clock = FakeClock()
    known = new_cell_id(clock)
    orphan = new_cell_id(clock)
    records = [_record(clock, known), _record(clock, orphan)]

    result = orphan_virtual_cells(records, frozenset({known}))

    assert result == (orphan,)


def test_orphan_virtual_cells_empty_when_every_record_is_known() -> None:
    clock = FakeClock()
    known = new_cell_id(clock)

    result = orphan_virtual_cells([_record(clock, known)], frozenset({known}))

    assert result == ()


# ──────────────────────────────────────────────────────────────────────────────
# orphan_real_leases (pure)
# ──────────────────────────────────────────────────────────────────────────────


def test_orphan_real_leases_excludes_a_lease_with_a_matching_release() -> None:
    clock = FakeClock()
    lease_id = new_lease_id(clock)
    task_id = new_task_id(clock)

    result = orphan_real_leases(
        [_leased_event(clock, lease_id, task_id)],
        [_released_event(clock, lease_id)],
        active_task_ids=frozenset(),
    )

    assert result == ()


def test_orphan_real_leases_excludes_a_lease_for_a_still_active_task() -> None:
    clock = FakeClock()
    lease_id = new_lease_id(clock)
    task_id = new_task_id(clock)

    result = orphan_real_leases(
        [_leased_event(clock, lease_id, task_id)], [], active_task_ids=frozenset({task_id})
    )

    assert result == ()


def test_orphan_real_leases_includes_a_lease_for_no_longer_active_task() -> None:
    clock = FakeClock()
    lease_id = new_lease_id(clock)
    task_id = new_task_id(clock)

    result = orphan_real_leases(
        [_leased_event(clock, lease_id, task_id)], [], active_task_ids=frozenset()
    )

    assert result == (lease_id,)


def test_orphan_real_leases_includes_an_internal_lease_with_no_task_id() -> None:
    clock = FakeClock()
    lease_id = new_lease_id(clock)

    result = orphan_real_leases(
        [_leased_event(clock, lease_id, None)], [], active_task_ids=frozenset()
    )

    assert result == (lease_id,)


# ──────────────────────────────────────────────────────────────────────────────
# sweep_orphans (effectful)
# ──────────────────────────────────────────────────────────────────────────────


async def _no_lease_found(lease_id: LeaseId) -> RealCellLease | None:
    """A LeaseFinder that never resolves a lease id."""
    del lease_id
    return None


async def _no_dormant_evicted(now: datetime) -> tuple[CellId, ...]:
    """A DormantEvictor that finds nothing expired."""
    del now
    return ()


def _make_sweep_deps(
    clock: FakeClock,
    backend: FakeCellBackend,
    trail: MemoryPheromoneTrail,
    identity: CellIdentity | None = None,
) -> SweepDeps:
    """Build a SweepDeps over fresh fakes; every Cell the backend holds counts as an orphan."""
    identity = identity if identity is not None else make_identity(clock=clock)
    undertaker = Undertaker(
        UndertakerDeps(
            backend=backend,
            grant_revoker=RecordingGrantRevoker(),
            wax_retirer=RecordingWaxRetirer(),
            leavings_remover=RecordingLeavingsRemover(),
            trail=trail,
            clock=clock,
            identity=identity,
        )
    )

    async def _nothing_known_live() -> frozenset[CellId]:
        return frozenset()

    return SweepDeps(
        undertaker=undertaker,
        backend=backend,
        hive_id=identity.hive_id,
        trail=trail,
        clock=clock,
        identity=identity,
        known_live_cells=_nothing_known_live,
        lease_finder=_no_lease_found,
        evict_dormant=_no_dormant_evicted,
    )


async def test_sweep_orphans_destroys_a_virtual_cell_the_backend_holds() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    backend = FakeCellBackend(clock)
    identity = make_identity(clock=clock)
    cell = await backend.provision(_make_spec(identity.hive_id))
    deps = _make_sweep_deps(clock, backend, trail, identity=identity)

    report = await sweep_orphans(deps, active_task_ids=frozenset())

    assert report.virtual_destroyed == (cell.id,)
    assert backend.destroy_calls == [cell.id]


async def test_sweep_orphans_releases_a_resolvable_orphaned_lease(tmp_path: Path) -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    backend = FakeCellBackend(clock)
    # trail= must be the same trail sweep_orphans queries, or lease.open()'s own cell.leased event
    # (written to whichever trail the lease was built with) is invisible to orphan_real_leases.
    lease = make_real_cell_lease(tmp_path, clock=clock, releaser=FakeLeaseReleaser(), trail=trail)
    await lease.open()

    async def _find(lease_id: LeaseId) -> RealCellLease | None:
        return lease if lease_id == lease.id else None

    deps = dataclasses.replace(_make_sweep_deps(clock, backend, trail), lease_finder=_find)

    report = await sweep_orphans(deps, active_task_ids=frozenset())

    assert report.real_released == (lease.id,)


async def test_sweep_orphans_reports_dormant_evictions() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    backend = FakeCellBackend(clock)
    evicted_id = new_cell_id(clock)

    async def _evict(now: datetime) -> tuple[CellId, ...]:
        del now
        return (evicted_id,)

    deps = dataclasses.replace(_make_sweep_deps(clock, backend, trail), evict_dormant=_evict)

    report = await sweep_orphans(deps, active_task_ids=frozenset())

    assert report.dormant_evicted == (evicted_id,)


async def test_sweep_orphans_records_one_summary_event() -> None:
    clock = FakeClock()
    trail = MemoryPheromoneTrail(clock)
    backend = FakeCellBackend(clock)
    deps = _make_sweep_deps(clock, backend, trail)

    await sweep_orphans(deps, active_task_ids=frozenset())

    events = await trail.query(TrailQuery(subject_id=deps.hive_id))
    assert len(events) == 1
    assert events[0].payload == {"virtual_destroyed": 0, "real_released": 0, "dormant_evicted": 0}
