"""Tests for hivemind.queen.ticks.honey: answering a Worker's or a Warden's HoneyQuery.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/honey.py (codingrules section 3); split by feature (14.2)
    from test_honey.py, which covers deposits and the dispatch hook. Every test seeds real Honey
    in a real SQLite Honey Store, places a real task in the Queen's Brood Chamber, and reads the
    Queen's HoneyResponse off the Warden's own raw end of the link.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.honey for the module under test.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the readers and ceilings asserted.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from builders.cells import make_cell
from builders.honey import open_test_honey_store_with_trail
from builders.honey_wire import (
    WireEnd,
    make_honey_access,
    make_honey_link,
    make_honey_query,
    seed_finding,
)
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CellKind, CombShieldLevel, HoneyClearance
from hivemind.honey_store import HoneyRetriever, HoneySearch, HoneyStoreError, RetrieverDeps
from hivemind.pheromone import SqlitePheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.inbox import to_inbox_item
from hivemind.queen.ticks.honey import (
    FOREIGN_TASK_REASON,
    NO_HONEY_STORE_REASON,
    NO_TASK_REASON,
    STORE_FAILED_REASON,
    UNKNOWN_TASK_REASON,
    WARDEN_MISMATCH_REASON,
    handle_honey_item,
)
from waggle.clock import Clock, FakeClock
from waggle.ids import WardenId, new_task_id, new_warden_id, new_worker_id
from waggle.messages import HoneyClearance as WireHoneyClearance
from waggle.messages.honey import HoneyQuery, HoneyResponse


@dataclass(frozen=True, slots=True)
class _Rig:
    """One Queen with a real, seeded Honey Store and one attached Warden link read by hand."""

    deps: QueenDeps
    link: WardenLink
    warden: WireEnd
    trail: SqlitePheromoneTrail

    @property
    def wardens(self) -> dict[WardenId, WardenLink]:
        return {self.link.warden_id: self.link}


async def _rig(tmp_path: Path, cell: Cell, *, seed_from: Cell | None = None) -> _Rig:
    """Build the rig for a Warden on `cell`, seeding one finding gathered on `seed_from`."""
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    access = make_honey_access(store, clock)
    await seed_finding(access, seed_from if seed_from is not None else cell, clock)
    deps, _link, _end = make_queen_deps(clock, cell=cell, honey=access)
    link, warden = make_honey_link(cell, clock, deps.identity.hive_id, deps.identity.node_id)
    return _Rig(deps=deps, link=link, warden=warden, trail=trail)


async def _place_task(
    rig: _Rig, cell: Cell | None = None, clearance: HoneyClearance = HoneyClearance.C1
) -> Task:
    """Submit one task at `clearance` and place it on `cell` (the rig's own Cell by default)."""
    graph = make_graph_draft({"a": ()})
    draft = graph.tasks[0].model_copy(update={"clearance": clearance})
    (task,) = await rig.deps.chamber.submit(graph.model_copy(update={"tasks": (draft,)}))
    placed_on = cell if cell is not None else rig.link.cell
    return await rig.deps.chamber.assign(task.id, rig.link.warden_id, placed_on.id, "a test")


async def _ask(rig: _Rig, query: HoneyQuery, deps: QueenDeps | None = None) -> HoneyResponse:
    """Hand `query` to the handler as the rig's Warden relayed it; return the correlated reply."""
    item = to_inbox_item(rig.warden.envelope_for(query), rig.link.warden_id)
    await handle_honey_item(deps if deps is not None else rig.deps, rig.wardens, item)
    reply = await rig.warden.next()
    assert reply.correlation_id == item.id
    assert isinstance(reply.payload, HoneyResponse)
    return reply.payload


def _virtual(clock: Clock | None = None, **overrides: object) -> Cell:
    return make_cell(kind=CellKind.VIRTUAL, clock=clock, **overrides)


async def test_a_workers_query_for_its_own_task_finds_the_seeded_finding(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, _virtual())
    task = await _place_task(rig)
    worker_id = new_worker_id(rig.deps.clock)

    response = await _ask(
        rig, make_honey_query(rig.deps.clock, requester=worker_id, task_id=task.id)
    )

    assert response.hits
    assert {hit.scope for hit in response.hits} == {"hive"}
    queried = await rig.trail.query(TrailQuery(kind="honey.queried"))
    assert [event.subject_id for event in queried] == [worker_id]


async def test_a_wardens_own_query_reads_as_itself(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, _virtual())

    response = await _ask(
        rig, make_honey_query(rig.deps.clock, requester=rig.link.warden_id, task_id=None)
    )

    assert response.hits


async def test_a_warden_asking_as_another_warden_is_answered_empty(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, _virtual())
    impostor = new_warden_id(rig.deps.clock)

    response = await _ask(rig, make_honey_query(rig.deps.clock, requester=impostor, task_id=None))

    assert response.hits == ()
    assert response.reason == WARDEN_MISMATCH_REASON


async def test_a_workers_query_without_a_task_is_answered_empty(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, _virtual())

    response = await _ask(rig, make_honey_query(rig.deps.clock, task_id=None))

    assert response.reason == NO_TASK_REASON


async def test_a_workers_query_for_an_unknown_task_is_answered_empty(tmp_path: Path) -> None:
    rig = await _rig(tmp_path, _virtual())

    response = await _ask(
        rig, make_honey_query(rig.deps.clock, task_id=new_task_id(rig.deps.clock))
    )

    assert response.hits == ()
    assert response.reason == UNKNOWN_TASK_REASON


async def test_a_workers_query_for_a_task_on_another_cell_is_answered_empty(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path, _virtual())
    task = await _place_task(rig, cell=_virtual(rig.deps.clock))

    response = await _ask(rig, make_honey_query(rig.deps.clock, task_id=task.id))

    assert response.hits == ()
    assert response.reason == FOREIGN_TASK_REASON


async def test_a_c1_task_never_sees_c2_honey_and_a_c2_task_does(tmp_path: Path) -> None:
    # Gathered on a borrowed (Real) Cell, the finding is C2 whatever it declared (ADR-0035).
    rig = await _rig(tmp_path, make_cell(kind=CellKind.REAL))
    c1_task = await _place_task(rig)
    c2_task = await _place_task(rig, clearance=HoneyClearance.C2)
    c2_query = {"max_clearance": WireHoneyClearance.C2}

    as_c1 = await _ask(rig, make_honey_query(rig.deps.clock, task_id=c1_task.id, **c2_query))
    as_c2 = await _ask(rig, make_honey_query(rig.deps.clock, task_id=c2_task.id, **c2_query))

    assert as_c1.hits == ()
    assert as_c1.filtered_count >= 1
    assert [hit.clearance for hit in as_c2.hits] == [WireHoneyClearance.C2] * len(as_c2.hits)
    assert as_c2.hits


async def test_a_night_veil_reader_is_capped_at_c1_and_leaves_no_trail(tmp_path: Path) -> None:
    clock = FakeClock()
    night_veil = _virtual(clock, comb_shield=CombShieldLevel.NIGHT_VEIL)
    rig = await _rig(tmp_path, night_veil, seed_from=_virtual(clock))
    task = await _place_task(rig, clearance=HoneyClearance.C2)

    response = await _ask(
        rig,
        make_honey_query(rig.deps.clock, task_id=task.id, max_clearance=WireHoneyClearance.C2),
    )

    assert response.hits
    assert all(hit.clearance is WireHoneyClearance.C1 for hit in response.hits)
    assert await rig.trail.query(TrailQuery(kind="honey.queried")) == ()


async def test_a_query_with_no_honey_store_is_answered_empty_with_the_reason(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path, _virtual())
    deps = dataclasses.replace(rig.deps, honey=None)

    response = await _ask(rig, make_honey_query(rig.deps.clock), deps)

    assert response.hits == ()
    assert response.reason == NO_HONEY_STORE_REASON


class _FailingRetriever(HoneyRetriever):
    """A retriever whose store fails mid-search."""

    async def search(self, search: HoneySearch) -> HoneyResponse:
        raise HoneyStoreError("The store failed mid-search.")


async def test_a_store_failure_during_a_search_is_answered_empty_never_raised(
    tmp_path: Path,
) -> None:
    rig = await _rig(tmp_path, _virtual())
    access = rig.deps.honey
    assert access is not None
    failing = _FailingRetriever(
        RetrieverDeps(access.store, access.identity, rig.deps.clock, access.retrieval)
    )
    deps = dataclasses.replace(rig.deps, honey=dataclasses.replace(access, retriever=failing))

    response = await _ask(
        rig, make_honey_query(rig.deps.clock, requester=rig.link.warden_id, task_id=None), deps
    )

    assert response.hits == ()
    assert response.reason == STORE_FAILED_REASON.format(code=HoneyStoreError.code)
