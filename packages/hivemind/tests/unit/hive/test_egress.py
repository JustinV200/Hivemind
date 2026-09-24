"""Tests for hivemind.hive.egress and the fake backend's EgressCutter: cut, restore, or say why not.

Roadmap step 10.6a (ADR-0035): isolating a Virtual Cell sets its egress to none except its Waggle
control link, and lifting the isolation gives its own policy back. The fake backend is the
reference `EgressCutter`; `LifecycleEgress` asks a tracked Cell's backend by its declared
capability, and reports UNTRACKED, UNSUPPORTED or FAILED rather than raising.

Fits into the Hive:
    Mirrors src/hivemind/hive/egress.py and the egress half of src/hivemind/hive/backends/fake.py
    (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.backends.base for EgressCutter and BackendCapabilities.can_cut_egress.
"""

from __future__ import annotations

import pytest
from builders.cells import make_identity
from builders.forage import make_capacity

from hivemind.hive import (
    BackendCapabilities,
    BackendCapabilityError,
    BackendRegistry,
    CellLifecycle,
    EgressCutter,
    EgressOutcome,
    FakeCellBackend,
    LifecycleEgress,
    VirtualCellSpec,
)
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_hive_id

_WITHOUT_EGRESS = BackendCapabilities(can_snapshot=False, can_pause=True, can_cut_egress=False)


def _spec(clock: FakeClock) -> VirtualCellSpec:
    """A plain terminal-only Virtual Cell request."""
    return VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=2.0,
        memory_bytes=2 * 1024**3,
        disk_bytes=10 * 1024**3,
        capacity=make_capacity(),
        hive_id=new_hive_id(clock),
    )


async def _tracked(backend: FakeCellBackend) -> tuple[LifecycleEgress, CellId]:
    """A LifecycleEgress over one lifecycle holding one provisioned Cell of `backend`."""
    clock = FakeClock()
    registry = BackendRegistry()
    registry.register("fake", lambda: backend)
    lifecycle = CellLifecycle(registry, MemoryPheromoneTrail(clock), clock, make_identity(clock))
    cell = await lifecycle.provision(_spec(clock), "fake")
    return LifecycleEgress(lifecycle), cell.id


def test_the_fake_backend_is_the_reference_egress_cutter() -> None:
    backend = FakeCellBackend(FakeClock())

    assert backend.capabilities.can_cut_egress
    assert isinstance(backend, EgressCutter)


async def test_the_fake_backend_cuts_and_restores_one_cells_egress() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    spec = _spec(clock)
    cell = await backend.provision(spec)

    await backend.cut_egress(cell.id)
    await backend.cut_egress(cell.id)  # Idempotent.
    cut = backend.egress_is_cut(cell.id)
    listed_while_cut = [record.cell_id for record in await backend.list_cells(spec.hive_id)]
    await backend.restore_egress(cell.id)

    assert cut and not backend.egress_is_cut(cell.id)
    assert backend.egress_calls == [("cut", cell.id), ("cut", cell.id), ("restore", cell.id)]
    # The Cell itself is untouched by the cut: still there, kept for forensics (ADR-0035).
    assert listed_while_cut == [cell.id]


async def test_the_fake_backend_refuses_a_cut_it_does_not_declare() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock, _WITHOUT_EGRESS)
    cell = await backend.provision(_spec(clock))

    with pytest.raises(BackendCapabilityError, match="egress cut"):
        await backend.cut_egress(cell.id)
    assert not backend.egress_is_cut(cell.id)


async def test_lifecycle_egress_cuts_a_tracked_cell_through_its_backend() -> None:
    backend = FakeCellBackend(FakeClock())
    egress, cell_id = await _tracked(backend)

    assert await egress.cut(cell_id) is EgressOutcome.CUT
    assert backend.egress_is_cut(cell_id)
    assert await egress.restore(cell_id) is EgressOutcome.RESTORED
    assert not backend.egress_is_cut(cell_id)


async def test_lifecycle_egress_leaves_a_cell_it_does_not_track_alone() -> None:
    backend = FakeCellBackend(FakeClock())
    egress, _cell_id = await _tracked(backend)

    # A Real Cell (the Hive Stand, a Swarm device) is never tracked, and never re-networked.
    assert await egress.cut(new_cell_id(FakeClock())) is EgressOutcome.UNTRACKED
    assert backend.egress_calls == []


async def test_lifecycle_egress_reports_a_backend_that_cannot_cut() -> None:
    backend = FakeCellBackend(FakeClock(), _WITHOUT_EGRESS)
    egress, cell_id = await _tracked(backend)

    assert await egress.cut(cell_id) is EgressOutcome.UNSUPPORTED
    assert backend.egress_calls == []  # Branching on the capability: the backend is never asked.
