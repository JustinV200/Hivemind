"""Unit tests for hivemind.cell.local.source: HiveStandSource's own lease preconditions.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/source.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). `tests/contracts/test_real_cell_source_contract.py` already runs every
    `RealCellSource` behaviour shared with the fake against this source; this module covers what
    is the Hive Stand's alone, namely the state of the host's filesystem at lease time.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.source for the module under test.
    - .claude/roadmap.md phase 3 step 3.11 for the scratch root and the disk reserve.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cells import make_hive_stand_config, make_identity, make_lease_request

from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.local.source import HiveStandSource
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock


async def test_a_first_lease_creates_a_scratch_root_that_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    """A brand-new Hive has never had a scratch root; its first lease must still succeed.

    The contract suite roots its source at pytest's own `tmp_path`, which always exists, so this
    is the one case only a dedicated test sees: before the fix, measuring free disk against the
    missing root raised FileNotFoundError (WinError 3 on Windows) and no Hive could ever take its
    first lease on a fresh install.
    """
    clock = FakeClock()
    scratch_root = tmp_path / "never-created" / "scratch"
    source = HiveStandSource(
        make_hive_stand_config(scratch_root),
        make_identity(clock=clock),
        MemoryPheromoneTrail(clock),
        clock,
    )
    cell = (await source.cells())[0]

    lease = await source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    assert scratch_root.is_dir()
    assert lease.scratch_root.is_dir()
    assert lease.scratch_root.parent == scratch_root


async def test_a_scratch_root_that_cannot_be_created_refuses_the_lease(tmp_path: Path) -> None:
    """A root the host will not make is a refusal with a reason, never a bare OSError."""
    clock = FakeClock()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    source = HiveStandSource(
        make_hive_stand_config(blocker / "scratch"),
        make_identity(clock=clock),
        MemoryPheromoneTrail(clock),
        clock,
    )
    cell = (await source.cells())[0]

    with pytest.raises(LeaseRefusedError, match="could not create the scratch root"):
        await source.lease(make_lease_request(clock=clock, cell_id=cell.id))


async def test_free_disk_under_the_reserve_refuses_the_lease(tmp_path: Path) -> None:
    """The reserve is measured against the root this source just made, not a missing path."""
    clock = FakeClock()
    source = HiveStandSource(
        # A reserve no real host can satisfy, so the check fires deterministically.
        make_hive_stand_config(tmp_path / "scratch", disk_reserve_mb=1_000_000_000),
        make_identity(clock=clock),
        MemoryPheromoneTrail(clock),
        clock,
    )
    cell = (await source.cells())[0]

    with pytest.raises(LeaseRefusedError, match="under the configured reserve"):
        await source.lease(make_lease_request(clock=clock, cell_id=cell.id))
