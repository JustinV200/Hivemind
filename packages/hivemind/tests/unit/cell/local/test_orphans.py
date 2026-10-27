"""Tests for hivemind.cell.local.orphans: giving back a lease a dead process left open.

A security boundary first: the lease id comes off the trail, so only a well-formed lease id may
name a directory to remove; anything else (`..`, a path, an absolute path) removes nothing at
all. A well-formed one has its scratch removed (or kept, under `keep_scratch`) and its
`cell.released` recorded, marked as Requeening's, with its touches outside scratch residual.

Fits into the Hive:
    Mirrors src/hivemind/cell/local/orphans.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.local.orphans for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hivemind.cell import CombShieldLevel, hive_stand_cell_id
from hivemind.cell.local.orphans import OrphanedLease, OrphanReleaser
from hivemind.cell.source import CellIdentity
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_lease_id, new_node_id

_CLOCK = FakeClock()


def _releaser(scratch_root: Path, *, keep_scratch: bool = False) -> OrphanReleaser:
    """A releaser over `scratch_root`, recording to a fresh trail."""
    node_id = new_node_id(_CLOCK)
    identity = CellIdentity(hive_id=new_hive_id(_CLOCK), node_id=node_id, actor="system")
    return OrphanReleaser(
        cell_id=hive_stand_cell_id(node_id),
        scratch_root=scratch_root,
        keep_scratch=keep_scratch,
        comb_shield=CombShieldLevel.MEADOW,
        identity=identity,
        trail=MemoryPheromoneTrail(_CLOCK),
        clock=_CLOCK,
    )


@pytest.mark.parametrize("lease_id", ["..", "../outside", "lease_../x", "/etc", "not-a-lease"])
async def test_an_id_that_is_no_lease_id_removes_nothing(lease_id: str, tmp_path: Path) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    canary = tmp_path / "canary"
    canary.mkdir()
    (root / "keep").mkdir()

    released = await _releaser(root).release_orphan(OrphanedLease(lease_id, "warden_x", None, 0))

    assert released is False
    assert canary.is_dir() and (root / "keep").is_dir()


@pytest.mark.parametrize(("keep_scratch", "gone"), [(False, True), (True, False)])
async def test_a_lease_is_given_back_and_its_release_recorded_as_requeenings(
    keep_scratch: bool, gone: bool, tmp_path: Path
) -> None:
    lease_id = new_lease_id(_CLOCK)
    (tmp_path / lease_id / "work").mkdir(parents=True)
    releaser = _releaser(tmp_path, keep_scratch=keep_scratch)

    released = await releaser.release_orphan(OrphanedLease(lease_id, "warden_x", "task_t", 2))

    assert released is True
    assert (tmp_path / lease_id).exists() is not gone
    [event] = await releaser.trail.query(TrailQuery(kind="cell.released"))
    assert event.payload["lease_id"] == lease_id
    assert event.payload["requeened"] is True
    assert event.payload["residual_paths"] == 2
    assert event.payload["is_restored"] is False  # Two paths outside scratch were never put back.
