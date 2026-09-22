"""Contract suite for LeavingsStore: one contract, run over both implementations.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the
    hivemind.cell.leavings.store_protocol.LeavingsStore contract and runs against both
    implementations that ship: hivemind.cell.leavings.store_memory.InMemoryLeavingsStore (over
    MemoryPheromoneTrail) and hivemind.cell.leavings.store_sqlite.SqliteLeavingsStore (over
    SqlitePheromoneTrail, both on the same tmp_path SQLite file). A new implementation joins the
    fixture's params and must pass here before it is used anywhere else (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cell.leavings.store_protocol for the LeavingsStore protocol under test.
    - packages/hivemind/tests/contracts/test_task_store_contract.py for the pattern this mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cells import make_leaving

from hivemind.cell.errors import LeavingAlreadyRemovedError, LeavingNotFoundError
from hivemind.cell.leavings import (
    InMemoryLeavingsStore,
    LeavingsStore,
    SqliteLeavingsStore,
)
from hivemind.common.errors import InvariantViolationError
from hivemind.common.sqlite import connect
from hivemind.pheromone import (
    CellEvent,
    MemoryPheromoneTrail,
    PheromoneTrail,
    SqlitePheromoneTrail,
    TrailQuery,
)
from waggle.clock import FakeClock
from waggle.ids import new_event_id, new_hive_id, new_node_id

_STORE_KINDS = ("memory", "sqlite")


def _make_cell_event(clock: FakeClock, cell_id: str, kind: str) -> CellEvent:
    """Build a well-formed CellEvent whose subject is `cell_id`, minting a fresh id and node."""
    return CellEvent(
        id=new_event_id(clock),
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=clock.now(),
        actor="system",
        kind=kind,
        subject_id=cell_id,
        payload={},
    )


@dataclass(frozen=True, slots=True)
class _StoreAndTrail:
    """A LeavingsStore and the PheromoneTrail it records events on, for the same fixture kind."""

    store: LeavingsStore
    trail: PheromoneTrail


@pytest.fixture(params=_STORE_KINDS)
async def store_and_trail(request: pytest.FixtureRequest, tmp_path: Path) -> _StoreAndTrail:
    """A (LeavingsStore, PheromoneTrail) pair of the parametrised kind, sharing one clock/file."""
    clock = FakeClock()
    if request.param == "memory":
        memory_trail = MemoryPheromoneTrail(clock)
        return _StoreAndTrail(store=InMemoryLeavingsStore(memory_trail), trail=memory_trail)
    # SQLite: two connections to the same file (ADR-0006 decision 1: two stores that write the
    # same file use separate connections); the trail's migration must run before the store's own.
    db_path = tmp_path / "hive.sqlite3"
    sqlite_trail = await SqlitePheromoneTrail.create(connect(db_path), clock)
    sqlite_store = await SqliteLeavingsStore.create(connect(db_path), clock)
    return _StoreAndTrail(store=sqlite_store, trail=sqlite_trail)


# ──────────────────────────────────────────────────────────────────────────────
# record_leaving / get_leaving
# ──────────────────────────────────────────────────────────────────────────────


async def test_record_leaving_then_get_leaving_returns_an_equal_leaving(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)
    event = _make_cell_event(clock, leaving.cell_id, "cell.left")

    await store_and_trail.store.record_leaving(leaving, event)
    result = await store_and_trail.store.get_leaving(leaving.cell_id, leaving.path)

    assert result == leaving


async def test_record_leaving_records_its_event_on_the_trail(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)
    event = _make_cell_event(clock, leaving.cell_id, "cell.left")

    await store_and_trail.store.record_leaving(leaving, event)

    results = await store_and_trail.trail.query(TrailQuery(subject_id=leaving.cell_id))
    assert results == (event,)


async def test_record_leaving_over_an_active_row_replaces_it_but_keeps_its_prior(
    store_and_trail: _StoreAndTrail,
) -> None:
    """Coordinator review, roadmap step 5.0a bug fix: never raise for an active duplicate.

    The ordinary case this covers is the same goal run twice: a second run leaves the same file
    while the first run's Leaving is still active. `record_leaving` must upsert, and the row's
    own `prior` -- the bytes the path held before any Leaving ever existed there -- must survive
    the replace so `hive cells leavings remove` still returns the host to its true left-as-found
    state, not to what the first run's own write produced.
    """
    clock = FakeClock()
    first = make_leaving(clock=clock, prior=b"what was there before either run")
    await store_and_trail.store.record_leaving(
        first, _make_cell_event(clock, first.cell_id, "cell.left")
    )
    second = make_leaving(
        clock=clock,
        cell_id=first.cell_id,
        path=first.path,
        sha256="1" * 64,
        size=99,
        reason="second run",
        prior=b"this must never be stored: it is not the true original",
    )

    await store_and_trail.store.record_leaving(
        second, _make_cell_event(clock, first.cell_id, "cell.left")
    )

    result = await store_and_trail.store.get_leaving(first.cell_id, first.path)
    assert result.sha256 == "1" * 64
    assert result.size == 99
    assert result.reason == "second run"
    assert result.prior == b"what was there before either run"


async def test_get_leaving_raises_when_nothing_is_recorded(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)

    with pytest.raises(LeavingNotFoundError):
        await store_and_trail.store.get_leaving(leaving.cell_id, leaving.path)


async def test_get_leaving_raises_once_the_row_is_removed(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)
    await store_and_trail.store.record_leaving(
        leaving, _make_cell_event(clock, leaving.cell_id, "cell.left")
    )
    await store_and_trail.store.mark_removed(
        leaving.cell_id,
        leaving.path,
        clock.now(),
        _make_cell_event(clock, leaving.cell_id, "cell.leaving_removed"),
    )

    with pytest.raises(LeavingNotFoundError):
        await store_and_trail.store.get_leaving(leaving.cell_id, leaving.path)


async def test_record_leaving_at_a_removed_paths_own_path_is_accepted(
    store_and_trail: _StoreAndTrail,
) -> None:
    """A removed row is never reopened (Leaving.removed_at's own docstring): a fresh one lands."""
    clock = FakeClock()
    first = make_leaving(clock=clock)
    await store_and_trail.store.record_leaving(
        first, _make_cell_event(clock, first.cell_id, "cell.left")
    )
    await store_and_trail.store.mark_removed(
        first.cell_id,
        first.path,
        clock.now(),
        _make_cell_event(clock, first.cell_id, "cell.leaving_removed"),
    )
    second = make_leaving(clock=clock, cell_id=first.cell_id, path=first.path, reason="again")

    await store_and_trail.store.record_leaving(
        second, _make_cell_event(clock, first.cell_id, "cell.left")
    )

    result = await store_and_trail.store.get_leaving(first.cell_id, first.path)
    assert result.reason == "again"
    assert result.removed_at is None


async def test_record_leaving_rejects_a_mismatched_event(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)
    wrong_subject_event = _make_cell_event(clock, make_leaving(clock=clock).cell_id, "cell.left")

    with pytest.raises(InvariantViolationError):
        await store_and_trail.store.record_leaving(leaving, wrong_subject_event)


# ──────────────────────────────────────────────────────────────────────────────
# list_leavings
# ──────────────────────────────────────────────────────────────────────────────


async def test_list_leavings_returns_only_active_rows_by_default(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    cell_id = make_leaving(clock=clock).cell_id
    active = make_leaving(clock=clock, cell_id=cell_id, path=Path("/outside/active.txt"))
    removed = make_leaving(clock=clock, cell_id=cell_id, path=Path("/outside/removed.txt"))
    for leaving in (active, removed):
        await store_and_trail.store.record_leaving(
            leaving, _make_cell_event(clock, cell_id, "cell.left")
        )
    await store_and_trail.store.mark_removed(
        cell_id,
        removed.path,
        clock.now(),
        _make_cell_event(clock, cell_id, "cell.leaving_removed"),
    )

    active_only = await store_and_trail.store.list_leavings(cell_id)
    every_row = await store_and_trail.store.list_leavings(cell_id, include_removed=True)

    assert [leaving.path for leaving in active_only] == [active.path]
    assert {leaving.path for leaving in every_row} == {active.path, removed.path}


async def test_list_leavings_for_an_unknown_cell_is_empty(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()

    result = await store_and_trail.store.list_leavings(make_leaving(clock=clock).cell_id)

    assert result == ()


# ──────────────────────────────────────────────────────────────────────────────
# list_all_leavings
# ──────────────────────────────────────────────────────────────────────────────


async def test_list_all_leavings_spans_every_cell_active_rows_by_default(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    first = make_leaving(clock=clock, path=Path("/outside/first.txt"))
    second = make_leaving(clock=clock, path=Path("/outside/second.txt"))
    removed = make_leaving(clock=clock, path=Path("/outside/removed.txt"))
    for leaving in (first, second, removed):
        await store_and_trail.store.record_leaving(
            leaving, _make_cell_event(clock, leaving.cell_id, "cell.left")
        )
    await store_and_trail.store.mark_removed(
        removed.cell_id,
        removed.path,
        clock.now(),
        _make_cell_event(clock, removed.cell_id, "cell.leaving_removed"),
    )

    active_only = await store_and_trail.store.list_all_leavings()
    every_row = await store_and_trail.store.list_all_leavings(include_removed=True)

    assert {leaving.path for leaving in active_only} == {first.path, second.path}
    assert {leaving.path for leaving in every_row} == {first.path, second.path, removed.path}
    # Every Cell's rows in one list, never scoped to a single cell_id (that is list_leavings).
    assert {leaving.cell_id for leaving in active_only} == {first.cell_id, second.cell_id}


async def test_list_all_leavings_is_empty_when_nothing_has_ever_been_recorded(
    store_and_trail: _StoreAndTrail,
) -> None:
    result = await store_and_trail.store.list_all_leavings()

    assert result == ()


async def test_list_all_leavings_is_ordered_by_cell_id_then_left_at_then_path(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    # Four distinct Cells (make_leaving mints a fresh cell_id per call), recorded out of order.
    leavings = [make_leaving(clock=clock, path=Path(f"/outside/{i}.txt")) for i in range(4)]
    for leaving in reversed(leavings):
        await store_and_trail.store.record_leaving(
            leaving, _make_cell_event(clock, leaving.cell_id, "cell.left")
        )

    result = await store_and_trail.store.list_all_leavings()

    keys = [(leaving.cell_id, leaving.left_at, str(leaving.path)) for leaving in result]
    assert keys == sorted(keys)


# ──────────────────────────────────────────────────────────────────────────────
# mark_removed
# ──────────────────────────────────────────────────────────────────────────────


async def test_mark_removed_sets_removed_at_and_records_its_event(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)
    await store_and_trail.store.record_leaving(
        leaving, _make_cell_event(clock, leaving.cell_id, "cell.left")
    )
    removed_at = clock.now()
    event = _make_cell_event(clock, leaving.cell_id, "cell.leaving_removed")

    result = await store_and_trail.store.mark_removed(
        leaving.cell_id, leaving.path, removed_at, event
    )

    assert result.removed_at == removed_at
    results = await store_and_trail.trail.query(TrailQuery(subject_id=leaving.cell_id))
    assert event in results


async def test_mark_removed_raises_when_no_row_exists(store_and_trail: _StoreAndTrail) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)

    with pytest.raises(LeavingNotFoundError):
        await store_and_trail.store.mark_removed(
            leaving.cell_id,
            leaving.path,
            clock.now(),
            _make_cell_event(clock, leaving.cell_id, "cell.leaving_removed"),
        )


async def test_mark_removed_twice_raises_leaving_already_removed(
    store_and_trail: _StoreAndTrail,
) -> None:
    clock = FakeClock()
    leaving = make_leaving(clock=clock)
    await store_and_trail.store.record_leaving(
        leaving, _make_cell_event(clock, leaving.cell_id, "cell.left")
    )
    await store_and_trail.store.mark_removed(
        leaving.cell_id,
        leaving.path,
        clock.now(),
        _make_cell_event(clock, leaving.cell_id, "cell.leaving_removed"),
    )

    with pytest.raises(LeavingAlreadyRemovedError):
        await store_and_trail.store.mark_removed(
            leaving.cell_id,
            leaving.path,
            clock.now(),
            _make_cell_event(clock, leaving.cell_id, "cell.leaving_removed"),
        )
