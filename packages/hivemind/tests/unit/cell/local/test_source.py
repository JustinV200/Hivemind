"""Unit tests for hivemind.cell.local.source: hive_stand_cell_id and HiveStandSource's own leases.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/source.py (codingrules section 3: tests/unit mirrors src/
    one-to-one). `tests/contracts/test_real_cell_source_contract.py` already runs every
    `RealCellSource` behaviour shared with the fake against this source; this module covers what
    is the Hive Stand's alone, namely the state of the host's filesystem at lease time, and
    `hive_stand_cell_id`'s own derivation (phase 7 handoff section 8 item 4).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.source for the module under test.
    - .claude/roadmap.md phase 3 step 3.11 for the scratch root and the disk reserve.
    - .claude/phase-7-handoff.md section 8 item 4 for why the Cell id is derived, not minted.
"""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest
from builders.cells import make_hive_stand_config, make_identity, make_lease_request

from hivemind.cell import SCRATCH_DIR_MODE
from hivemind.cell.errors import LeaseRefusedError
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cell.local.source import HiveStandSource, hive_stand_cell_id
from hivemind.pheromone import MemoryPheromoneTrail
from waggle.clock import FakeClock
from waggle.ids import IdKind, NodeId, new_node_id, parse_id


def _leavings(clock: FakeClock) -> InMemoryLeavingsStore:
    """A throwaway leavings store: none of this module's tests ever release a lease."""
    return InMemoryLeavingsStore(MemoryPheromoneTrail(clock))


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
        _leavings(clock),
    )
    cell = (await source.cells())[0]

    lease = await source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    assert scratch_root.is_dir()
    assert lease.scratch_root.is_dir()
    assert lease.scratch_root.parent == scratch_root


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits; Windows ignores them")
async def test_a_lease_scratch_directory_is_private_to_the_hives_own_user(tmp_path: Path) -> None:
    # A lease's scratch holds the Exoskeleton's display cookie and a browser profile's cookies;
    # created with the process umask (0755 typically), any other local account could read them.
    clock = FakeClock()
    source = HiveStandSource(
        make_hive_stand_config(tmp_path / "scratch"),
        make_identity(clock=clock),
        MemoryPheromoneTrail(clock),
        clock,
        _leavings(clock),
    )
    cell = (await source.cells())[0]

    lease = await source.lease(make_lease_request(clock=clock, cell_id=cell.id))

    assert stat.S_IMODE(lease.scratch_root.stat().st_mode) == SCRATCH_DIR_MODE


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
        _leavings(clock),
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
        _leavings(clock),
    )
    cell = (await source.cells())[0]

    with pytest.raises(LeaseRefusedError, match="under the configured reserve"):
        await source.lease(make_lease_request(clock=clock, cell_id=cell.id))


# ──────────────────────────────────────────────────────────────────────────────
# hive_stand_cell_id: derived, not minted (phase 7 handoff item 4)
# ──────────────────────────────────────────────────────────────────────────────


def test_hive_stand_cell_id_is_deterministic_in_the_node_id() -> None:
    """The same node id always derives the same Cell id -- pure, no clock, no randomness."""
    node_id = new_node_id(FakeClock())

    assert hive_stand_cell_id(node_id) == hive_stand_cell_id(node_id)


def test_hive_stand_cell_id_shares_the_node_ids_own_ulid() -> None:
    """Only the kind prefix changes: `node_<ulid>` becomes `cell_<the same ulid>`."""
    node_id = new_node_id(FakeClock())

    cell_id = hive_stand_cell_id(node_id)

    assert cell_id.removeprefix("cell_") == node_id.removeprefix("node_")


def test_hive_stand_cell_id_returns_a_well_formed_cell_id() -> None:
    """The result satisfies waggle's own CellId validator, not just this module's own shape."""
    node_id = new_node_id(FakeClock())

    cell_id = hive_stand_cell_id(node_id)

    assert parse_id(cell_id, IdKind.CELL) == cell_id


def test_hive_stand_cell_id_differs_for_two_different_node_ids() -> None:
    """Two nodes never collide onto the same Hive Stand Cell id."""
    clock = FakeClock()
    first_node = new_node_id(clock)
    clock.advance(1.0)  # A distinct timestamp too, not only distinct randomness.
    second_node = new_node_id(clock)

    assert hive_stand_cell_id(first_node) != hive_stand_cell_id(second_node)


# ──────────────────────────────────────────────────────────────────────────────
# HiveStandSource reports the derived id, stable across every construction on this node
# ──────────────────────────────────────────────────────────────────────────────


def _source(tmp_path: Path, clock: FakeClock, node_id: NodeId) -> HiveStandSource:
    """Build a HiveStandSource over `tmp_path`, identified by `node_id` (see make_identity)."""
    trail = MemoryPheromoneTrail(clock)
    return HiveStandSource(
        make_hive_stand_config(tmp_path),
        make_identity(clock=clock, node_id=node_id),
        trail,
        clock,
        _leavings(clock),
    )


async def test_hive_stand_source_reports_the_cell_id_hive_stand_cell_id_derives(
    tmp_path: Path,
) -> None:
    """The Cell HiveStandSource hands out carries exactly `hive_stand_cell_id(identity.node_id)`."""
    clock = FakeClock()
    node_id = new_node_id(clock)
    source = _source(tmp_path, clock, node_id)

    (cell,) = await source.cells()

    assert cell.id == hive_stand_cell_id(node_id)


async def test_two_sources_built_from_the_same_node_id_report_the_same_cell_id(
    tmp_path: Path,
) -> None:
    """Two HiveStandSources over the same node id -- two processes on one Hive Stand -- agree.

    This is the fix for phase 7 handoff item 4: before it, `HiveStandSource.__init__` minted a
    fresh, random `CellId` on every construction, so a second `hive run` (a second
    `HiveStandSource` instance, exactly what this test builds) never matched the first's rows.
    """
    clock = FakeClock()
    node_id = new_node_id(clock)

    first = _source(tmp_path, clock, node_id)
    second = _source(tmp_path, clock, node_id)

    (first_cell,) = await first.cells()
    (second_cell,) = await second.cells()
    assert first_cell.id == second_cell.id
