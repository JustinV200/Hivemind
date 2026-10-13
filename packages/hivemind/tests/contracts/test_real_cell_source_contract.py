"""Contract suite for RealCellSource: one contract, run over every implementation.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.cell.source.RealCellSource contract and runs against every implementation registered
    in `_HARNESSES` below: `hivemind.cell.fake.FakeCellSource` (phase 3 step 3.10),
    `hivemind.cell.local.HiveStandSource`, the Hive Stand's own source (phase 3 step 3.11), and
    `hivemind.swarm.SwarmSource`, an enrolled device's source (phase 11 step 11.2), driven against
    a scripted Pollen Packet over an in-memory link. A new RealCellSource implementation adds a
    `SourceHarness` here and must pass this suite before it is used anywhere else (codingrules
    14.3).

    A harness builds a source with its own trail so tests can assert on the events it wrote
    without knowing how the source is constructed; every harness's source holds at least one
    Cell, so tests always work from `(await source.cells())[0]` rather than a hardcoded id.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.source for the RealCellSource protocol under test.
    - hivemind.cell.fake for FakeCellSource, the first implementation registered here.
    - hivemind.cell.local for HiveStandSource, the Hive Stand's own implementation.
    - hivemind.swarm.source for SwarmSource, and builders.swarm for the scripted device it leases.
    - packages/hivemind/tests/contracts/test_pheromone_trail_contract.py for the pattern this
      suite's harness-per-implementation shape mirrors.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pytest
from builders.cells import make_cell, make_hive_stand_config, make_identity, make_lease_request
from builders.swarm import make_link_rig, make_source

from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.fake import FakeCellSource
from hivemind.cell.lease_state import LeaseState
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cell.local.source import HiveStandSource
from hivemind.cell.source import RealCellSource
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.pheromone.trail.protocol import PheromoneTrail, TrailQuery
from waggle.clock import FakeClock


@dataclass(frozen=True, slots=True)
class SourceRig:
    """One built RealCellSource plus the trail it was given, so tests can assert on events.

    `close` ends whatever the source needs running (the swarm harness's link and scripted device);
    the fixture awaits it after the test.
    """

    source: RealCellSource
    trail: PheromoneTrail
    close: Callable[[], Awaitable[None]] | None = None


class SourceHarness(Protocol):
    """Build a RealCellSource of one kind, holding at least one Cell."""

    async def build(self, clock: FakeClock, tmp_path: Path) -> SourceRig:
        """Build a source holding at least one Cell, plus the trail it was given.

        Args:
            clock: Source of every minted id this source and its leases use.
            tmp_path: A real, empty directory a source that touches the filesystem roots its
                scratch directories under; unused by a source that does not.
        """
        ...


class _FakeHarness:
    """Builds a FakeCellSource over a single freshly built Cell."""

    async def build(self, clock: FakeClock, tmp_path: Path) -> SourceRig:
        trail = MemoryPheromoneTrail(clock)
        cell = make_cell(clock=clock)
        source = FakeCellSource([cell], make_identity(clock=clock), trail, clock)
        return SourceRig(source=source, trail=trail)


class _LocalHarness:
    """Builds a HiveStandSource over a real, enabled config rooted at a real temp directory."""

    async def build(self, clock: FakeClock, tmp_path: Path) -> SourceRig:
        trail = MemoryPheromoneTrail(clock)
        config = make_hive_stand_config(tmp_path)
        leavings = InMemoryLeavingsStore(trail)
        source = HiveStandSource(config, make_identity(clock=clock), trail, clock, leavings)
        return SourceRig(source=source, trail=trail)


class _SwarmHarness:
    """Builds a SwarmSource for an enrolled device, leasing from a scripted Pollen Packet.

    The scripted device grants any holder (the contract's requests name a random Warden) and
    SCRATCH access, the default request's level, so the clauses read as for any other source.
    """

    async def build(self, clock: FakeClock, tmp_path: Path) -> SourceRig:
        link_rig = await make_link_rig(clock=clock)
        source = make_source(link_rig)
        return SourceRig(source=source, trail=link_rig.trail, close=link_rig.close)


_HARNESSES: dict[str, SourceHarness] = {
    "fake": _FakeHarness(),
    "local": _LocalHarness(),
    "swarm": _SwarmHarness(),
}


@pytest.fixture(params=sorted(_HARNESSES))
async def rig(request: pytest.FixtureRequest, tmp_path: Path) -> AsyncIterator[SourceRig]:
    """One SourceRig per registered RealCellSource implementation, on a fresh FakeClock."""
    built = await _HARNESSES[request.param].build(FakeClock(), tmp_path)
    yield built
    if built.close is not None:
        await built.close()


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
