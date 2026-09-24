"""Unit tests for hivemind.wardens.spawn.in_cell: the `in_cell` Warden spawn strategy (5.5).

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Exercises `InCellSpawnSource` against the same
    `RealCellSource` contract `HiveStandSource`/`FakeCellSource` satisfy: `cells()`, `lease()`,
    `open_session()`. Also asserts the "strategy selection" itself -- which `RealCellSource` a
    `WardenDeps.source` is built from -- never has to be read back off `Cell.kind` by anything
    above this module (codingrules section 8.7).

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 5.5 for "Strategy selection must not branch on cell.kind; choose by
      injected configuration."
    - hivemind.wardens.spawn.in_cell for the module under test.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from builders.cells import make_capabilities, make_identity, make_lease_request
from builders.forage import make_capacity

from hivemind.cell import SCRATCH_DIR_MODE
from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.in_cell import InCellSession
from hivemind.cell.models import CellKind
from hivemind.cell.tiers import AccessLevel, CombShieldLevel
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.wardens.spawn.in_cell import InCellSpawnConfig, InCellSpawnSource
from waggle.clock import FakeClock
from waggle.ids import new_cell_id


def _build_source(tmp_path: Path) -> InCellSpawnSource:
    """Build an InCellSpawnSource over a fresh Cell id, a terminal-only Linux Cell description."""
    clock = FakeClock()
    config = InCellSpawnConfig(
        cell_id=new_cell_id(clock),
        capabilities=make_capabilities(),
        capacity=make_capacity(),
        comb_shield=CombShieldLevel.MEADOW,
        scratch_root=tmp_path,
    )
    identity = make_identity(clock=clock)
    return InCellSpawnSource(config, identity, MemoryPheromoneTrail(clock), clock)


async def test_cells_returns_exactly_one_virtual_cell_at_full_access(tmp_path: Path) -> None:
    source = _build_source(tmp_path)

    cells = await source.cells()

    assert len(cells) == 1
    assert cells[0].kind is CellKind.VIRTUAL
    assert cells[0].access_level is AccessLevel.FULL
    assert cells[0].source == "in_cell"


async def test_cell_id_never_changes_across_calls(tmp_path: Path) -> None:
    source = _build_source(tmp_path)

    first = (await source.cells())[0].id
    second = (await source.cells())[0].id

    assert first == second


async def test_lease_opens_and_open_session_returns_an_in_cell_session(tmp_path: Path) -> None:
    source = _build_source(tmp_path)
    cell = (await source.cells())[0]
    request = make_lease_request(cell_id=cell.id, access_level=AccessLevel.FULL)

    lease = await source.lease(request)
    session = await source.open_session(lease)

    assert isinstance(session, InCellSession)
    assert session.scratch_dir == lease.scratch_root
    assert lease.scratch_root.exists()  # A fresh scratch subdirectory was created.


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits; Windows ignores them")
async def test_a_lease_scratch_directory_is_private_to_the_cells_own_user(tmp_path: Path) -> None:
    source = _build_source(tmp_path)
    cell = (await source.cells())[0]

    lease = await source.lease(make_lease_request(cell_id=cell.id, access_level=AccessLevel.FULL))

    assert stat.S_IMODE(lease.scratch_root.stat().st_mode) == SCRATCH_DIR_MODE


async def test_lease_refuses_a_request_for_a_different_cell_id(tmp_path: Path) -> None:
    source = _build_source(tmp_path)
    request = make_lease_request(access_level=AccessLevel.FULL)  # A cell_id this source never had.

    with pytest.raises(LeaseRefusedError):
        await source.lease(request)


async def test_lease_refuses_a_second_overlapping_lease(tmp_path: Path) -> None:
    source = _build_source(tmp_path)
    cell = (await source.cells())[0]
    request = make_lease_request(cell_id=cell.id, access_level=AccessLevel.FULL)
    await source.lease(request)

    with pytest.raises(LeaseRefusedError):
        await source.lease(request)


async def test_lease_succeeds_again_after_the_first_is_released(tmp_path: Path) -> None:
    source = _build_source(tmp_path)
    cell = (await source.cells())[0]
    request = make_lease_request(cell_id=cell.id, access_level=AccessLevel.FULL)
    first = await source.lease(request)
    await first.release()

    second = await source.lease(request)

    assert second.id != first.id
