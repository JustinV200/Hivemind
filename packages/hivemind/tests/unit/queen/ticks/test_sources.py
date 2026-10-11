"""Tests for hivemind.queen.ticks.sources: a Warden's loaded or lost model source, applied.

Fits into the Hive:
    Mirrors src/hivemind/queen/ticks/sources.py (codingrules section 3). Roadmap step 8.3.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.ticks.sources for the module under test.
    - hivemind.queen.ticks.liveness for the dispatch that reaches it.
"""

from __future__ import annotations

from builders.queen import make_queen_deps
from builders.supervision import make_inbox_item

from hivemind.forage import ModelCost, UnknownSourceError
from hivemind.pheromone import TrailQuery
from hivemind.queen.deps import WardenLink
from hivemind.queen.inbox import to_inbox_item
from hivemind.queen.ticks.liveness import handle_infrastructure_item
from hivemind.queen.ticks.sources import handle_source_item
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import Hop, wrap
from waggle.ids import CellId, new_cell_id
from waggle.messages.base import WaggleMessage
from waggle.messages.forage import SourceAdded, SourceRef, SourceRemovalCause, SourceRemoved


def _item(link: WardenLink, payload: WaggleMessage, deps_clock: object) -> InboxItem:
    """Wrap `payload` as `link`'s Warden sends it, and as the Queen's inbox receives it."""
    hop = Hop(sender=link.warden_id, recipient=link.hop.sender, node_id=link.hop.node_id)
    return to_inbox_item(wrap(payload, hop, clock=deps_clock), link.warden_id)  # type: ignore[arg-type]


def _ref(cell_id: CellId, source_id: str = "local_small") -> SourceRef:
    return SourceRef(
        source_id=source_id, provider="local", model="small-model", host_cell_id=cell_id
    )


def _added(cell_id: CellId, **overrides: object) -> SourceAdded:
    fields: dict[str, object] = {
        "cell_id": cell_id,
        "server_id": "box",
        "source": _ref(cell_id),
        "grade": 2,
        "context_window": 4096,
        "seats_total": 3,
        "vram_bytes": 1_000,
        "capabilities": ("json_mode",),
        "reason": "Loaded on box within this Warden's ceilings.",
    }
    fields.update(overrides)
    return SourceAdded(**fields)


def _removed(cell_id: CellId, cause: SourceRemovalCause) -> SourceRemoved:
    return SourceRemoved(
        cell_id=cell_id, server_id="box", source=_ref(cell_id), cause=cause, reason="gone"
    )


async def test_an_added_source_reaches_the_map_the_ledger_and_the_trail() -> None:
    deps, link, _end = make_queen_deps()
    item = _item(link, _added(link.cell.id), deps.clock)

    handled = await handle_source_item(deps, {link.warden_id: link}, item)

    assert handled is True
    source = deps.map.get("local_small")
    assert source.spec.host_cell_id == link.cell.id
    assert (source.spec.seats, source.abundance.seats_free) == (3, 3)
    assert source.spec.vram_bytes_required == 1_000
    assert deps.ledger.seats.free("local_small") == 3
    events = [e for e in await deps.trail.query(TrailQuery()) if e.kind == "forage.source_added"]
    assert len(events) == 1
    assert events[0].subject_id == link.cell.id
    assert events[0].payload["server_id"] == "box"
    assert events[0].payload["reporter"] == "queen"


async def test_an_added_source_the_map_already_lists_keeps_its_prices_and_measured_grade() -> None:
    deps, link, _end = make_queen_deps()
    listed = deps.map.sources()[0]
    spec = listed.spec.model_copy(
        update={
            "provider": "local",
            "cost": ModelCost(cost_per_million_input_usd=0.5),
            "measured_grade": 4,
        }
    )
    await deps.map.add_source(listed.model_copy(update={"source_id": "local_small", "spec": spec}))
    item = _item(link, _added(link.cell.id), deps.clock)

    await handle_source_item(deps, {link.warden_id: link}, item)

    after = deps.map.get("local_small")
    assert after.spec.cost.cost_per_million_input_usd == 0.5
    assert after.spec.measured_grade == 4
    assert after.spec.host_cell_id == link.cell.id


async def test_a_removed_source_leaves_the_map_and_its_seats_drop_to_zero() -> None:
    deps, link, _end = make_queen_deps()
    wardens = {link.warden_id: link}
    await handle_source_item(deps, wardens, _item(link, _added(link.cell.id), deps.clock))

    removal = _removed(link.cell.id, SourceRemovalCause.SERVER_STOPPED)
    await handle_source_item(deps, wardens, _item(link, removal, deps.clock))

    try:
        deps.map.get("local_small")
    except UnknownSourceError:
        gone = True
    else:
        gone = False
    assert gone
    assert deps.ledger.seats.free("local_small") == 0
    events = [e for e in await deps.trail.query(TrailQuery()) if e.kind == "forage.source_removed"]
    assert events[0].payload["cause"] == "SERVER_STOPPED"


async def test_a_report_about_another_cell_is_refused() -> None:
    deps, link, _end = make_queen_deps()
    elsewhere = new_cell_id(deps.clock)
    item = _item(link, _added(elsewhere), deps.clock)

    handled = await handle_source_item(deps, {link.warden_id: link}, item)

    assert handled is True  # Consumed, not applied.
    assert all(s.source_id != "local_small" for s in deps.map.sources())
    assert deps.ledger.seats.free("local_small") is None


async def test_a_source_that_names_a_different_host_is_refused() -> None:
    deps, link, _end = make_queen_deps()
    wrong_host = _added(link.cell.id, source=_ref(new_cell_id(deps.clock)))

    await handle_source_item(deps, {link.warden_id: link}, _item(link, wrong_host, deps.clock))

    assert all(s.source_id != "local_small" for s in deps.map.sources())


async def test_a_report_from_an_unattached_warden_is_refused() -> None:
    deps, link, _end = make_queen_deps()
    item = _item(link, _added(link.cell.id), deps.clock)

    await handle_source_item(deps, {}, item)

    assert deps.ledger.seats.free("local_small") is None


async def test_any_other_payload_is_not_handled() -> None:
    deps, link, _end = make_queen_deps()

    assert await handle_source_item(deps, {link.warden_id: link}, make_inbox_item()) is False


async def test_the_infrastructure_dispatch_reaches_the_source_handler() -> None:
    deps, link, _end = make_queen_deps()
    item = _item(link, _added(link.cell.id), deps.clock)

    handled = await handle_infrastructure_item(deps, {link.warden_id: link}, item, {}, {})

    assert handled is True
    assert deps.map.get("local_small").spec.host_cell_id == link.cell.id
