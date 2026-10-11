"""Tests for hivemind.queen.forage.evict: the Queen reclaims only seats she exported.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/evict.py (codingrules section 3). Roadmap step 8.3.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.evict for the module under test.
"""

from __future__ import annotations

from builders.forage import make_source
from builders.queen import WardenEnd, make_queen_deps

from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.forage import LocalPoolReport, evict_source
from waggle.ids import new_cell_id


async def _hosting(exported: int | None) -> tuple[QueenDeps, WardenLink, WardenEnd]:
    """A Queen whose map lists `local_small` hosted on the attached Warden's Cell."""
    deps, link, end = make_queen_deps()
    await deps.map.add_source(
        make_source(source_id="local_small", provider="local", host_cell_id=link.cell.id)
    )
    if exported is not None:
        await deps.ledger.report_local_pool(
            LocalPoolReport(
                warden_id=link.warden_id,
                cell_id=link.cell.id,
                sub_bees_active=0,
                model_vram_bytes=0,
                model_disk_bytes=0,
                seats_exported=exported,
            )
        )
    return deps, link, end


async def test_exported_seats_let_the_queen_send_the_order_and_record_it() -> None:
    deps, link, end = await _hosting(exported=3)

    sent = await evict_source(deps, link, "local_small", seats=2)

    assert sent is True
    await end.pump_until(lambda: bool(end.source_evicts))
    order = end.source_evicts[0]
    assert (order.source.source_id, order.seats_to_reclaim) == ("local_small", 2)
    assert order.cell_id == link.cell.id
    events = [e for e in await deps.trail.query(TrailQuery()) if e.kind == "forage.source_evicted"]
    assert len(events) == 1
    assert events[0].payload["seats_to_reclaim"] == 2


async def test_the_order_never_asks_for_more_than_was_exported() -> None:
    deps, link, end = await _hosting(exported=1)

    await evict_source(deps, link, "local_small", seats=5)

    await end.pump_until(lambda: bool(end.source_evicts))
    assert end.source_evicts[0].seats_to_reclaim == 1


async def test_no_local_pool_report_means_no_eviction() -> None:
    deps, link, _end = await _hosting(exported=None)

    assert await evict_source(deps, link, "local_small", seats=1) is False
    assert all(e.kind != "forage.source_evicted" for e in await deps.trail.query(TrailQuery()))


async def test_zero_exported_seats_means_no_eviction() -> None:
    deps, link, _end = await _hosting(exported=0)

    assert await evict_source(deps, link, "local_small", seats=1) is False


async def test_an_unknown_source_is_not_evicted() -> None:
    deps, link, _end = await _hosting(exported=2)

    assert await evict_source(deps, link, "ghost", seats=1) is False


async def test_a_source_hosted_on_another_cell_is_not_evicted() -> None:
    deps, link, _end = await _hosting(exported=2)
    await deps.map.add_source(
        make_source(source_id="elsewhere", provider="local", host_cell_id=new_cell_id(deps.clock))
    )

    assert await evict_source(deps, link, "elsewhere", seats=1) is False


async def test_a_non_positive_seat_count_sends_nothing() -> None:
    deps, link, _end = await _hosting(exported=2)

    assert await evict_source(deps, link, "local_small", seats=0) is False
