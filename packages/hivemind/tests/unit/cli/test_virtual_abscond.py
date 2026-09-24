"""Tests for hivemind.cli.readback.virtual_abscond: a separate Absconding and the Night Veil.

A `hive cells abscond` process starts from the Hive's stores alone: the Queen that provisioned the
Cell is gone, so the process holds no segment of it and never saw its grant issued. Its pass still
leaves only a Night Veil Cell's skeleton on the trail: the Cell is known by its tier label, its
grant is filed under it before the Undertaker revokes it, and the purge runs once it is gone. A
MEADOW Cell's Absconding records exactly what it did before.

Fits into the Hive:
    Mirrors src/hivemind/cli/readback/virtual_abscond.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.readback.virtual_abscond for the module under test.
    - tests.e2e.test_night_veil_boundary for the same end inside a running Hive's own process.
"""

from __future__ import annotations

from pathlib import Path

from builders.forage import make_grant
from builders.night_veil import night_veil_manifest, tier_spec

from hivemind.cell import CombShieldLevel
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.compose.virtual_cells import VirtualCellsParts, build_virtual_cells
from hivemind.cli.readback.virtual_abscond import AbscondDeps, run_abscond
from hivemind.forage.grant_state import GrantState
from hivemind.pheromone import MemoryPheromoneTrail, TrailQuery
from hivemind.queen import ForageLedger
from hivemind.queen.cluster import InMemoryOrderStore
from waggle.clock import FakeClock
from waggle.ids import CellId

_CLEANUP_COUNTS = {"grants_revoked": 1, "wax_retired": 0, "leavings_removed": 0}


async def _abscond_one_cell(
    tmp_path: Path, tier: CombShieldLevel
) -> tuple[VirtualCellsParts, CellId, MemoryPheromoneTrail]:
    """Leave one Cell at `tier` with one live grant behind, then run a fresh process's pass."""
    manifest = night_veil_manifest(tmp_path)
    clock = FakeClock()
    durable = MemoryPheromoneTrail(clock)
    # Built over the plain trail, as the command builds it: a boundary holding nothing yet.
    parts = build_virtual_cells(manifest, durable, clock)
    assert parts is not None
    cell = await parts.registry.get("fake").provision(tier_spec(manifest, tier))
    ledger = ForageLedger()
    await ledger.record_grant(make_grant(GrantState.ACTIVE, clock, cell_id=cell.id))

    summary = await run_abscond(
        AbscondDeps(
            manifest=manifest,
            trail=durable,
            ledger=ledger,
            orders=InMemoryOrderStore(),
            clock=clock,
            virtual_cells=parts,
            leavings=InMemoryLeavingsStore(durable),
        )
    )

    assert summary.containers_destroyed == 1
    assert summary.grants_revoked == 1
    assert summary.left_as_found
    return parts, cell.id, durable


async def test_a_separate_absconding_leaves_only_a_night_veil_cells_skeleton(
    tmp_path: Path,
) -> None:
    parts, cell_id, durable = await _abscond_one_cell(tmp_path, CombShieldLevel.NIGHT_VEIL)

    # The grant's revocation stayed in the Cell's segment and went with it; its destruction
    # crossed as its skeleton, and the purge recorded that it ran.
    events = await durable.query(TrailQuery())
    assert [(e.kind, e.subject_id) for e in events] == [
        ("cell.destroyed", cell_id),
        ("cell.purged", cell_id),
    ]
    assert events[0].payload == _CLEANUP_COUNTS
    assert events[1].payload == {"events_purged": 2, "side_channel_records_purged": 0}
    assert parts.night_veil.segments.held_cells() == ()


async def test_a_meadow_cells_absconding_records_what_it_always_did(tmp_path: Path) -> None:
    parts, cell_id, durable = await _abscond_one_cell(tmp_path, CombShieldLevel.MEADOW)

    events = await durable.query(TrailQuery())
    assert [e.kind for e in events] == ["forage.revoked", "cell.destroyed"]
    assert events[1].subject_id == cell_id
    assert events[1].payload == _CLEANUP_COUNTS
    assert not parts.night_veil.segments.owns(cell_id)
