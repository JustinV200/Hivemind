"""End-to-end test for phase 7 handoff item 4: the Hive Stand's Cell id is stable across processes.

Two separate "processes" (here, two separate `hivemind.cell.local.HiveStandSource`/`HoneyAccess`
builds from the very same manifest and the very same SQLite file, driven one after the other,
each in its own `asyncio.run`, the way `hivemind.cli.compose.build_hive` and every store `open_*`
helper must be called -- module docstring of `tests.e2e.test_honey_compounds`) must lease the
SAME Hive Stand Cell id, derived from the manifest's own `[hive] node_id`
(`hivemind.cell.hive_stand_cell_id`) rather than minted fresh on each build. Before that fix, a
fresh random Cell id every `hive run` meant a Honey row filed at `cell:<id>` scope by one process
could never be found by the next: `cell_id` is exactly what `cell:<id>` scopes, Cell Wax and
Leavings are all keyed by. This test leases the Hive Stand for real in "run 1", deposits and
ripens one Cell-scoped Honey row there, releases the lease, then leases the Hive Stand again in
"run 2" (same manifest, same database file, a fresh `HiveStandSource` instance standing in for a
second `hive run` process) and confirms it is handed the identical Cell id and can read back run
1's row at that same `cell:<id>` scope.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/phase-7-handoff.md section 8 item 4 for the gap this test proves closed.
    - hivemind.cell.local.source for hive_stand_cell_id, the function under test here end to end.
    - tests.e2e.test_honey_compounds for the real-Hive-Stand-lease pattern this test follows,
      and for why `build_hive`/every store `open_*` helper must run outside any event loop.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest

from hivemind.cell import AccessLevel, CombShieldLevel, HoneyClearance, hive_stand_cell_id
from hivemind.cell.lease import LeaseRequest, RealCellLease
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cell.local import HiveStandSource
from hivemind.cli.compose.deps import build_fanner, build_hive_stand_source, build_provider_registry
from hivemind.cli.compose.honey import build_honey_access
from hivemind.cli.stores import build_forage_map, open_honey_store, open_trail
from hivemind.honey_store import HoneyAccess, NectarOrigin, NectarSubmission, ReadFilter
from hivemind.manifest import load_manifest
from waggle.clock import SystemClock
from waggle.ids import CellId, new_warden_id
from waggle.messages.honey import NectarKind

pytestmark = pytest.mark.e2e

_TITLE = "Run 1's own Cell history"  # heuristic_title (no RIPENER call) echoes this verbatim.
_CONTENT = b"Run 1 recorded this note about the Hive Stand's own Cell."  # Well under 400 chars.


def _open(manifest_path: Path, clock: SystemClock) -> tuple[HiveStandSource, HoneyAccess]:
    """Build a fresh HiveStandSource and HoneyAccess over `manifest_path`'s own database file.

    Every builder called here is synchronous but does its own `asyncio.run` internally for setup
    (`open_trail`, `open_honey_store`; `hivemind.cli.compose.build_hive`'s own rule), so this
    function itself must run outside any event loop -- exactly like `build_hive`. Called once per
    "run" in this module, each call standing in for a separate `hive run` process reopening the
    same manifest and database file.
    """
    manifest = load_manifest(manifest_path, {})
    db = manifest.resolve_path(manifest.hive.db)
    trail = open_trail(db)
    # A throwaway leavings store: this test's own lease never writes outside scratch.
    source = build_hive_stand_source(manifest, trail, clock, InMemoryLeavingsStore(trail))
    forage_map = build_forage_map(manifest, clock)
    registry = build_provider_registry(manifest, {}, clock, forage_map, None)
    fanner = build_fanner(manifest, forage_map, trail, clock)
    honey = build_honey_access(manifest, open_honey_store(db), registry, fanner, clock)
    return source, honey


async def _lease(source: HiveStandSource, clock: SystemClock) -> RealCellLease:
    """Lease the Hive Stand's one Cell, exactly as a real Warden's own `start()` would."""
    cell = (await source.cells())[0]
    request = LeaseRequest(
        cell_id=cell.id,
        holder=new_warden_id(clock),
        task_id=None,
        access_level=AccessLevel.SCRATCH,
        allowed_paths=(),
    )
    return await source.lease(request)


async def _deposit_and_ripen(
    source: HiveStandSource, honey: HoneyAccess, clock: SystemClock
) -> CellId:
    """Run 1: lease the Hive Stand, deposit and ripen one Cell-scoped finding, then release."""
    lease = await _lease(source, clock)
    submission = NectarSubmission(
        kind=NectarKind.FINDING,
        origin=NectarOrigin.CELL_WAX,  # Cell Wax history is always scoped to its own Cell.
        media_type="text/plain",
        title=_TITLE,
        content=_CONTENT,
        task_id=None,
        cell_id=lease.cell_id,
        observed_at=clock.now(),
        declared=HoneyClearance.C1,
        from_borrowed_cell=True,  # The Hive Stand is borrowed, like any other Real Cell.
        tier=CombShieldLevel.MEADOW,
    )
    await honey.intake.submit(submission)
    # Short content (module docstring): the heuristic summary fires, no RIPENER model call.
    await honey.ripener.run_pass()
    await lease.release()
    return lease.cell_id


async def _lease_and_read_back(
    source: HiveStandSource, honey: HoneyAccess, clock: SystemClock
) -> tuple[CellId, tuple[str, ...]]:
    """Run 2: lease the Hive Stand again and read back whatever sits at its own `cell:<id>` scope.

    Args:
        source: A fresh HiveStandSource over the same manifest and database file as run 1's own.
        honey: That build's own HoneyAccess, read from here.
        clock: Injected time source for the lease id this call mints.

    Returns:
        This lease's own Cell id, and every Honey row title found at that Cell's own scope.
    """
    lease = await _lease(source, clock)
    everything = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)
    rows = await honey.store.list_honey(
        everything, scope_prefix=f"cell:{lease.cell_id}", limit=10, offset=0
    )
    await lease.release()
    return lease.cell_id, tuple(row.title for row in rows)


def test_two_runs_from_the_same_manifest_lease_the_same_hive_stand_cell_id_and_compound(
    tmp_path: Path,
) -> None:
    """Run 2 leases the identical Cell id run 1 did, and reads back what run 1 left there."""
    manifest_path = fake_manifest(tmp_path)
    node_id = load_manifest(manifest_path, {}).hive.node_id
    expected_cell_id = hive_stand_cell_id(node_id)

    # Two separate builds from the same manifest and database file (module docstring), each
    # standing in for a separate `hive run` process -- built here, outside any event loop.
    clock = SystemClock()
    first_source, first_honey = _open(manifest_path, clock)
    second_source, second_honey = _open(manifest_path, clock)

    # Run 1, then run 2, sequentially, each its own asyncio.run (module docstring).
    first_cell_id = asyncio.run(_deposit_and_ripen(first_source, first_honey, clock))
    second_cell_id, titles = asyncio.run(_lease_and_read_back(second_source, second_honey, clock))

    assert first_cell_id == expected_cell_id, "run 1 did not lease the derived Hive Stand Cell id"
    assert second_cell_id == expected_cell_id, "run 2 did not lease the same Cell id as run 1"
    assert _TITLE in titles, "run 2 could not find run 1's own cell:<id>-scoped Honey row"
