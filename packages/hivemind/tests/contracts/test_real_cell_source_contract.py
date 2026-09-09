"""Contract suite for RealCellSource: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.cell.source.RealCellSource contract and runs against every implementation registered
    in `_HARNESSES` below: `hivemind.cell.fake.FakeCellSource` now (phase 3 step 3.10);
    `hivemind.cell.local.HiveStandSource` joins in phase 3 step 3.11. A new RealCellSource
    implementation adds a `SourceHarness` here and must pass this suite before it is used
    anywhere else (codingrules 14.3).

    A harness builds a source with its own trail so tests can assert on the events it wrote
    without knowing how the source is constructed; every harness's source holds at least one
    Cell, so tests always work from `(await source.cells())[0]` rather than a hardcoded id.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.source for the RealCellSource protocol under test.
    - hivemind.cell.fake for FakeCellSource, the first implementation registered here.
    - packages/hivemind/tests/contracts/test_pheromone_trail_contract.py for the pattern this
      suite's harness-per-implementation shape mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pytest
from builders.cells import make_cell, make_identity, make_lease_request

from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.source import RealCellSource
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import PheromoneTrail, TrailQuery
from waggle.clock import Clock, FakeClock


@dataclass(frozen=True, slots=True)
class SourceRig:
    """One built RealCellSource plus the trail it was given, so tests can assert on events."""

    source: RealCellSource
    trail: PheromoneTrail


class SourceHarness(Protocol):
    """Build a RealCellSource of one kind, holding at least one Cell."""

    def build(self, clock: Clock) -> SourceRig:
        """Build a source holding at least one Cell, plus the trail it was given."""
        ...


class _FakeHarness:
    """Builds a FakeCellSource over a single freshly built Cell."""

    def build(self, clock: Clock) -> SourceRig:
        trail = MemoryPheromoneTrail(clock)
        cell = make_cell(clock=clock)
        source = FakeCellSource([cell], make_identity(clock=clock), trail, clock)
        return SourceRig(source=source, trail=trail)


_HARNESSES: dict[str, SourceHarness] = {"fake": _FakeHarness()}


@pytest.fixture(params=sorted(_HARNESSES))
def rig(request: pytest.FixtureRequest) -> SourceRig:
    """One SourceRig per registered RealCellSource implementation, on a fresh FakeClock."""
    return _HARNESSES[request.param].build(FakeClock())


async def test_cells_returns_at_least_one_cell(rig: SourceRig) -> None:
    cells = await rig.source.cells()

    assert len(cells) >= 1


async def test_lease_then_release_leaves_the_cell_leasable_again(rig: SourceRig) -> None:
    clock = FakeClock()
    cell = (await rig.source.cells())[0]

    first = await rig.source.lease(make_lease_request(clock=clock, cell_id=cell.id))
    await first.release()
    second = await rig.source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    assert first.state is LeaseState.RELEASED
    assert second.state is LeaseState.OPEN


async def test_double_lease_on_the_same_cell_is_refused(rig: SourceRig) -> None:
    clock = FakeClock()
    cell = (await rig.source.cells())[0]
    await rig.source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    with pytest.raises(LeaseRefusedError):
        await rig.source.lease(make_lease_request(clock=clock, cell_id=cell.id))


async def test_lease_and_release_events_land_on_the_trail_in_order(rig: SourceRig) -> None:
    clock = FakeClock()
    cell = (await rig.source.cells())[0]
    lease = await rig.source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    await lease.release()

    events = await rig.trail.query(TrailQuery(subject_id=cell.id))
    assert [event.kind for event in events] == ["cell.leased", "cell.released"]


async def test_open_session_returns_a_session_scoped_to_the_lease(rig: SourceRig) -> None:
    clock = FakeClock()
    cell = (await rig.source.cells())[0]
    lease = await rig.source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    session = await rig.source.open_session(lease)

    assert session.scratch_dir == lease.scratch_root
    assert session.is_open
