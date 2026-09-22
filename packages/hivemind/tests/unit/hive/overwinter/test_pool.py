"""Tests for hivemind.hive.overwinter.pool: OverwinterPool's admit/claim/evict/view.

Fits into the Hive:
    Mirrors src/hivemind/hive/overwinter/pool.py (codingrules section 3: tests/unit mirrors
    src/ one-to-one).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.hive.overwinter.pool for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.cells import make_cell, make_identity
from builders.forage import make_capacity

from hivemind.cell import Cell, CellKind, CombShieldLevel
from hivemind.hive.backends.fake import FakeCellBackend
from hivemind.hive.errors import InvalidCellTransitionError
from hivemind.hive.models import VirtualCellSpec
from hivemind.hive.overwinter.policy import OverwinterConfig
from hivemind.hive.overwinter.pool import OverwinterPool
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from waggle.clock import FakeClock


class _ScrubRecorder:
    """A Scrubber that records every Cell it was asked to scrub, in order."""

    def __init__(self) -> None:
        """Create a _ScrubRecorder with nothing scrubbed yet."""
        self.scrubbed: list[Cell] = []

    async def __call__(self, cell: Cell) -> None:
        """Record `cell`; see hivemind.hive.overwinter.pool.Scrubber."""
        self.scrubbed.append(cell)


def _spec(**overrides: object) -> VirtualCellSpec:
    """Build a valid VirtualCellSpec: a modest base-ubuntu image, overridable per test."""
    fields: dict[str, object] = {
        "image": "base-ubuntu",
        "cpu_cores": 1.0,
        "memory_bytes": 1 * 1024**3,
        "disk_bytes": 5 * 1024**2,  # 5 MB, so view()'s disk math is easy to check by hand.
        "capacity": make_capacity(),
        "hive_id": make_identity().hive_id,
    }
    fields.update(overrides)
    return VirtualCellSpec(**fields)


def _config(**overrides: object) -> OverwinterConfig:
    """Build an OverwinterConfig with generous bounds, overridable per test."""
    fields: dict[str, object] = {
        "enabled": True,
        "max_cells": 4,
        "max_per_image": 2,
        "max_dormant_s": 3600.0,
        "disk_budget_mb": 8192,
    }
    fields.update(overrides)
    return OverwinterConfig(**fields)  # type: ignore[arg-type]


def _make_pool(
    clock: FakeClock, backend: FakeCellBackend, **config_overrides: object
) -> OverwinterPool:
    """Build an OverwinterPool over fresh fakes."""
    return OverwinterPool(
        backend=backend,
        clock=clock,
        config=_config(**config_overrides),
        trail=MemoryPheromoneTrail(clock),
        identity=make_identity(clock=clock),
    )


async def test_admit_pauses_the_backend_and_records_the_cell_dormant() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)
    spec = _spec()
    scrub = _ScrubRecorder()

    dormant = await pool.admit(cell, spec, scrub=scrub)

    assert scrub.scrubbed == [cell]
    assert backend.pause_calls == [cell.id]
    assert dormant.cell.id == cell.id
    assert dormant.dormant_until == dormant.dormant_since + timedelta(seconds=3600.0)


async def test_admit_refuses_a_night_veil_cell() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.NIGHT_VEIL)

    with pytest.raises(InvalidCellTransitionError):
        await pool.admit(cell, _spec(), scrub=_ScrubRecorder())

    # Refused before any effect: never scrubbed, never paused (module docstring's own ordering).
    assert backend.pause_calls == []


async def test_claim_returns_none_when_nothing_matches() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend)

    result = await pool.claim("base-ubuntu")

    assert result is None


async def test_claim_resumes_and_removes_the_oldest_matching_cell() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend)
    older = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)
    await pool.admit(older, _spec(), scrub=_ScrubRecorder())
    clock.advance(10.0)
    newer = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)
    await pool.admit(newer, _spec(), scrub=_ScrubRecorder())

    claimed = await pool.claim("base-ubuntu")

    assert claimed is not None
    assert claimed.cell.id == older.id
    assert backend.resume_calls == [older.id]
    # Removed from the pool: a second claim for the same image now finds only the newer one.
    second = await pool.claim("base-ubuntu")
    assert second is not None
    assert second.cell.id == newer.id


async def test_evict_expired_destroys_only_cells_past_their_own_deadline() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend, max_dormant_s=100.0)
    expiring = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)
    await pool.admit(expiring, _spec(), scrub=_ScrubRecorder())
    clock.advance(50.0)
    fresh = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)
    await pool.admit(
        fresh, _spec(), scrub=_ScrubRecorder()
    )  # dormant_until is 100s from now, not 50s.

    evicted = await pool.evict_expired(clock.now() + timedelta(seconds=60.0))

    assert evicted == [expiring.id]
    assert backend.destroy_calls == [expiring.id]
    # The fresh Cell is still claimable: evict_expired only removed the one past its own deadline.
    assert (await pool.claim("base-ubuntu")).cell.id == fresh.id  # type: ignore[union-attr]


async def test_view_reports_total_per_image_and_disk_used() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend)
    await pool.admit(
        make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW),
        _spec(image="base-ubuntu", disk_bytes=5 * 1024**2),
        scrub=_ScrubRecorder(),
    )
    await pool.admit(
        make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW),
        _spec(image="desktop-ubuntu", disk_bytes=3 * 1024**2),
        scrub=_ScrubRecorder(),
    )

    view = pool.view()

    assert view.total == 2
    assert view.per_image == {"base-ubuntu": 1, "desktop-ubuntu": 1}
    assert view.disk_used_mb == 8


async def test_dormant_candidates_reports_every_admitted_cell() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend)
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)
    await pool.admit(cell, _spec(image="base-ubuntu"), scrub=_ScrubRecorder())

    candidates = pool.dormant_candidates()

    assert len(candidates) == 1
    assert candidates[0].cell_id == cell.id
    assert candidates[0].image == "base-ubuntu"
    assert candidates[0].comb_shield is CombShieldLevel.MEADOW


async def test_admit_all_idle_admits_every_pair_and_skips_night_veil() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    pool = _make_pool(clock, backend)
    fine = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)
    night_veil = make_cell(
        kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.NIGHT_VEIL
    )

    admitted = await pool.admit_all_idle(
        [(fine, _spec()), (night_veil, _spec())], scrub=_ScrubRecorder()
    )

    assert admitted == (fine.id,)
    assert pool.view().total == 1


async def test_admit_records_cell_overwintered_event() -> None:
    clock = FakeClock()
    backend = FakeCellBackend(clock)
    trail = MemoryPheromoneTrail(clock)
    pool = OverwinterPool(
        backend=backend,
        clock=clock,
        config=_config(),
        trail=trail,
        identity=make_identity(clock=clock),
    )
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock, comb_shield=CombShieldLevel.MEADOW)

    await pool.admit(cell, _spec(), scrub=_ScrubRecorder())

    events = await trail.query(TrailQuery(kind="cell.overwintered"))
    assert len(events) == 1
    assert events[0].subject_id == cell.id
