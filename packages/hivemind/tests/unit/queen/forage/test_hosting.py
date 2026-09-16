"""Tests for hivemind.queen.forage.hosting: write_hosting_plan.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/hosting.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.hosting for the module under test.
"""

from __future__ import annotations

import dataclasses

from builders.cells import make_capabilities, make_cell
from builders.forage import make_capacity, make_host_capacity, make_source
from builders.queen import make_queen_deps

from hivemind.cell import Cell
from hivemind.forage import ForageMap, GoalBudgets, HostingPlan, ModelSource, RoyalReserve
from hivemind.forage.models.capacity import GpuInfo
from hivemind.forage.models.sources import Abundance
from hivemind.pheromone import ForageEvent, TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.forage.hosting import write_hosting_plan
from hivemind.queen.forage.ledger import ForageLedger
from waggle.clock import FakeClock

_GIB = 1024**3
_WORKER_MODEL = "test-model"  # Matches make_queen_deps's own default "worker" binding.


def _cell(
    clock: FakeClock, *, can_host_model: bool = False, free_vram_bytes: int = 8 * _GIB
) -> Cell:
    capacity = make_capacity(
        host=make_host_capacity(
            gpus=(GpuInfo(name="gpu0", vram_bytes=16 * _GIB, vram_free_bytes=free_vram_bytes),)
        )
    )
    return make_cell(
        clock=clock,
        capacity=capacity,
        capabilities=make_capabilities(can_host_model=can_host_model),
    )


async def _deps_with_shared_headroom(clock: FakeClock, cell: Cell) -> QueenDeps:
    """Build QueenDeps with shared-seat headroom > 0, so only a test's own factor forces local."""
    ledger = ForageLedger(reserve=RoyalReserve(seats=0, headroom_fraction=0.0))
    await ledger.seats.set_capacity("shared_headroom", 5)
    deps, _link, _warden_end = make_queen_deps(clock=clock, cell=cell, ledger=ledger)
    return deps


def _with_map(deps: QueenDeps, sources: tuple[ModelSource, ...], clock: FakeClock) -> QueenDeps:
    """Replace `deps.map` with a fresh ForageMap over `sources`, keeping every other field."""
    return dataclasses.replace(deps, map=ForageMap(sources, clock=clock))


def _worker_chain(plan: HostingPlan) -> tuple[str, tuple[str, ...]]:
    """Pull the WORKER slot's (primary, fallbacks) out of a HostingPlan for a plain assertion."""
    (worker_slot,) = [s for s in plan.slots if s.slot.value == "WORKER"]
    return worker_slot.chain.primary, worker_slot.chain.fallbacks


async def test_local_source_is_preferred_first_but_shared_stays_a_fallback() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=False)  # No forcing factor: pure ranking decides.
    local = make_source(
        source_id="src_local", provider="fake", model=_WORKER_MODEL, host_cell_id=cell.id
    )
    shared = make_source(
        source_id="src_shared", provider="fake", model=_WORKER_MODEL, host_cell_id=None
    )
    deps = _with_map(await _deps_with_shared_headroom(clock, cell), (local, shared), clock)

    plan = await write_hosting_plan(cell, deps)

    primary, fallbacks = _worker_chain(plan)
    assert primary == "src_local"
    assert "src_shared" in fallbacks  # Still reachable, not excluded outright.


async def test_vram_too_small_excludes_the_local_source() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=False, free_vram_bytes=1)  # Not enough for the model.
    local = make_source(
        source_id="src_local",
        provider="fake",
        model=_WORKER_MODEL,
        host_cell_id=cell.id,
        vram_bytes_required=8 * _GIB,
    )
    shared = make_source(
        source_id="src_shared", provider="fake", model=_WORKER_MODEL, host_cell_id=None
    )
    deps = _with_map(await _deps_with_shared_headroom(clock, cell), (local, shared), clock)

    plan = await write_hosting_plan(cell, deps)

    primary, fallbacks = _worker_chain(plan)
    assert primary == "src_shared"
    assert "src_local" not in (primary, *fallbacks)


async def test_a_throttled_source_falls_behind_an_available_one() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=True)  # Both candidates are local either way.
    good = make_source(
        source_id="src_good", provider="fake", model=_WORKER_MODEL, host_cell_id=cell.id
    )
    throttled = make_source(
        source_id="src_throttled",
        provider="fake",
        model=_WORKER_MODEL,
        host_cell_id=cell.id,
        abundance=Abundance(seats_free=0),
    )
    deps = _with_map(await _deps_with_shared_headroom(clock, cell), (throttled, good), clock)

    plan = await write_hosting_plan(cell, deps)

    primary, fallbacks = _worker_chain(plan)
    assert primary == "src_good"
    assert fallbacks == ("src_throttled",)


async def test_disconnection_need_forces_local_only() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=True)  # A Nuc candidate: must survive disconnection.
    local = make_source(
        source_id="src_local", provider="fake", model=_WORKER_MODEL, host_cell_id=cell.id
    )
    shared = make_source(
        source_id="src_shared", provider="fake", model=_WORKER_MODEL, host_cell_id=None
    )
    # Plenty of shared headroom, so this is provably not the seat-pressure branch.
    deps = _with_map(await _deps_with_shared_headroom(clock, cell), (local, shared), clock)

    plan = await write_hosting_plan(cell, deps)

    primary, fallbacks = _worker_chain(plan)
    assert primary == "src_local"
    assert fallbacks == ()  # The shared source is excluded outright, not merely ranked behind.


async def test_seat_pressure_forces_local_only_even_without_disconnection_need() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=False)  # Isolates seat pressure as the sole factor.
    local = make_source(
        source_id="src_local", provider="fake", model=_WORKER_MODEL, host_cell_id=cell.id
    )
    shared = make_source(
        source_id="src_shared", provider="fake", model=_WORKER_MODEL, host_cell_id=None
    )
    # No shared seat capacity declared at all: ForageLedger.headroom().shared_seats is 0.
    deps, _link, _warden_end = make_queen_deps(clock=clock, cell=cell)
    deps = _with_map(deps, (local, shared), clock)

    plan = await write_hosting_plan(cell, deps)

    primary, fallbacks = _worker_chain(plan)
    assert primary == "src_local"
    assert fallbacks == ()


async def test_cost_cap_excludes_a_non_free_source() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=False)
    free = make_source(
        source_id="src_free", provider="fake", model=_WORKER_MODEL, host_cell_id=None
    )
    priced = make_source(
        source_id="src_priced",
        provider="fake",
        model=_WORKER_MODEL,
        host_cell_id=None,
        cost={"cost_per_million_input_usd": 1.0},
    )
    deps = _with_map(await _deps_with_shared_headroom(clock, cell), (free, priced), clock)
    deps = dataclasses.replace(
        deps, budgets=GoalBudgets(spend_cap_usd=0.0, token_budget=1, max_sub_bees=1)
    )

    plan = await write_hosting_plan(cell, deps)

    primary, fallbacks = _worker_chain(plan)
    assert primary == "src_free"
    assert "src_priced" not in (primary, *fallbacks)


async def test_write_hosting_plan_records_forage_plan_written_and_the_ledgers_own_copy() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=False)
    source = make_source(source_id="src_1", provider="fake", model=_WORKER_MODEL, host_cell_id=None)
    deps = _with_map(await _deps_with_shared_headroom(clock, cell), (source,), clock)

    plan = await write_hosting_plan(cell, deps)

    assert deps.ledger.decisions.plan_for(cell.id) == plan
    events = await deps.trail.query(TrailQuery())
    (event,) = [e for e in events if e.kind == "forage.plan_written"]
    assert isinstance(event, ForageEvent)
    assert event.subject_id == cell.id
    assert event.payload["revision"] == 0


async def test_write_hosting_plan_increments_revision_on_a_second_call() -> None:
    clock = FakeClock()
    cell = _cell(clock, can_host_model=False)
    source = make_source(source_id="src_1", provider="fake", model=_WORKER_MODEL, host_cell_id=None)
    deps = _with_map(await _deps_with_shared_headroom(clock, cell), (source,), clock)

    await write_hosting_plan(cell, deps)
    second = await write_hosting_plan(cell, deps)

    assert second.revision == 1
