"""Tests for LifecycleCellCut: the Sting Cut's Virtual side over a real lifecycle and fake backend.

Roadmap step 13.4a. A tracked Cell is snapshotted only where its backend can snapshot (the fake
cannot: nothing is kept), its key is forgotten at the readiness gate, its scrub reports whether its
Warden confirmed, and its teardown destroys it. An untracked Cell (a Real one) gets nothing done.

Fits into the Hive:
    Mirrors src/hivemind/queen/sting_cut/virtual.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.sting_cut.virtual for the module under test.
"""

from __future__ import annotations

import pytest
from builders.cells import make_cell, make_identity
from builders.forage import make_capacity

from hivemind.cell import Cell
from hivemind.hive import BackendRegistry, CellScrubError, FakeCellBackend, VirtualCellSpec
from hivemind.hive.lifecycle import CellLifecycle
from hivemind.hive.overwinter import Scrubber
from hivemind.pheromone import MemoryPheromoneTrail
from hivemind.queen.cell_gate import QueenReadinessGate
from hivemind.queen.sting_cut import ScrubResult
from hivemind.queen.sting_cut.virtual import LifecycleCellCut
from waggle.clock import FakeClock
from waggle.ids import new_hive_id


async def _scrub_fails(cell: Cell) -> None:
    """A scrub its Warden never confirmed."""
    raise CellScrubError(cell.id, "no answer")


async def _scrub_ok(cell: Cell) -> None:
    """A scrub its Warden confirmed."""
    del cell


@pytest.mark.parametrize(
    ("scrub", "result"),
    [(_scrub_ok, ScrubResult.CONFIRMED), (_scrub_fails, ScrubResult.UNCONFIRMED)],
)
async def test_a_tracked_cell_is_scrubbed_forgotten_and_torn_down(
    scrub: Scrubber, result: ScrubResult
) -> None:
    clock = FakeClock()
    registry = BackendRegistry()
    registry.register("fake", lambda: FakeCellBackend(clock))
    lifecycle = CellLifecycle(registry, MemoryPheromoneTrail(clock), clock, make_identity(clock))
    spec = VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=1024**3,
        capacity=make_capacity(),
        hive_id=new_hive_id(clock),
    )
    cell = await lifecycle.provision(spec, "fake")
    gate = QueenReadinessGate()
    await gate.expect(cell.id, "ab" * 32)
    cut = LifecycleCellCut(lifecycle, gate, scrub, clock)
    real = make_cell(clock=clock)

    assert cut.tracks(cell.id) and not cut.tracks(real.id)
    assert await cut.keep_evidence(cell.id) is None  # The fake backend cannot snapshot.
    assert await cut.scrub(cell) is result
    assert await cut.forget_key(cell.id) and gate.expected_key(cell.id) is None
    assert not await cut.forget_key(real.id) and not await cut.teardown(real.id)
    assert await cut.teardown(cell.id) and lifecycle.status_of(cell.id) is None
