"""Unit tests for hivemind.cell.fake.source: FakeCellSource's inventory, lease and release."""

from __future__ import annotations

import pytest
from builders.cells import make_cell, make_identity, make_lease_request

from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.lease_state import LeaseState
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import TrailQuery
from waggle.clock import FakeClock


async def test_cells_returns_the_built_inventory() -> None:
    clock = FakeClock()
    cell = make_cell(clock=clock)
    source = FakeCellSource([cell], make_identity(clock=clock), MemoryPheromoneTrail(clock), clock)

    assert await source.cells() == (cell,)


async def test_lease_then_release_leaves_the_cell_leasable_again() -> None:
    clock = FakeClock()
    cell = make_cell(clock=clock)
    trail = MemoryPheromoneTrail(clock)
    source = FakeCellSource([cell], make_identity(clock=clock), trail, clock)
    request = make_lease_request(clock=clock, cell_id=cell.id)

    first = await source.lease(request)
    await first.release()
    second = await source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    assert first.id != second.id
    assert second.state is LeaseState.OPEN


async def test_lease_on_unknown_cell_is_refused() -> None:
    clock = FakeClock()
    source = FakeCellSource([], make_identity(clock=clock), MemoryPheromoneTrail(clock), clock)

    with pytest.raises(LeaseRefusedError):
        await source.lease(make_lease_request(clock=clock))


async def test_double_lease_on_the_same_cell_is_refused() -> None:
    clock = FakeClock()
    cell = make_cell(clock=clock)
    source = FakeCellSource([cell], make_identity(clock=clock), MemoryPheromoneTrail(clock), clock)
    request = make_lease_request(clock=clock, cell_id=cell.id)
    await source.lease(request)

    with pytest.raises(LeaseRefusedError):
        await source.lease(make_lease_request(clock=clock, cell_id=cell.id))


async def test_lease_and_release_events_land_on_the_trail_in_order() -> None:
    clock = FakeClock()
    cell = make_cell(clock=clock)
    trail = MemoryPheromoneTrail(clock)
    source = FakeCellSource([cell], make_identity(clock=clock), trail, clock)
    lease = await source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    await lease.release()

    events = await trail.query(TrailQuery(subject_id=cell.id))
    assert [event.kind for event in events] == ["cell.leased", "cell.released"]


async def test_open_session_returns_a_session_bound_to_the_leases_scratch_root() -> None:
    clock = FakeClock()
    cell = make_cell(clock=clock)
    source = FakeCellSource([cell], make_identity(clock=clock), MemoryPheromoneTrail(clock), clock)
    lease = await source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    session = await source.open_session(lease)

    assert session.scratch_dir == lease.scratch_root
    assert session.is_open
