"""Tests for hivemind.queen.forage.ceilings: set_ceilings, change_ceilings.

Fits into the Hive:
    Mirrors src/hivemind/queen/forage/ceilings.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.forage.ceilings for the module under test.
"""

from __future__ import annotations

from builders.queen import make_queen_deps

from hivemind.forage.models.pools import Ceilings
from hivemind.pheromone import ForageEvent, TrailQuery
from hivemind.queen.forage.ceilings import change_ceilings, set_ceilings


def _ceilings(**overrides: object) -> Ceilings:
    fields: dict[str, object] = {
        "max_sub_bees": 4,
        "model_vram_bytes": 1_000,
        "model_disk_bytes": 2_000,
        "loadable_sources": (),
        "exportable_seats": 1,
    }
    fields.update(overrides)
    return Ceilings(**fields)


async def test_set_ceilings_records_it_in_the_ledger() -> None:
    deps, link, _warden_end = make_queen_deps()
    ceilings = _ceilings()

    await set_ceilings(link, ceilings, deps)

    assert deps.ledger.decisions.ceilings_for(link.warden_id) == ceilings


async def test_set_ceilings_sends_ceilings_set_over_the_wardens_own_link() -> None:
    deps, link, warden_end = make_queen_deps()
    ceilings = _ceilings()

    await set_ceilings(link, ceilings, deps)

    message = await warden_end.wait_for_ceilings_set()
    assert message.holder == link.warden_id
    assert message.cell_id == link.cell.id
    assert message.revision == 0
    assert message.ceilings.max_sub_bees == ceilings.max_sub_bees


async def test_set_ceilings_records_forage_ceilings_set_with_revision_zero() -> None:
    deps, link, _warden_end = make_queen_deps()

    await set_ceilings(link, _ceilings(), deps)

    events = await deps.trail.query(TrailQuery())
    (event,) = [e for e in events if e.kind == "forage.ceilings_set"]
    assert isinstance(event, ForageEvent)
    assert event.subject_id == link.warden_id
    assert event.payload["revision"] == 0
    assert event.payload["old_max_sub_bees"] is None
    assert event.payload["new_max_sub_bees"] == 4


async def test_change_ceilings_increments_the_revision_and_replaces_the_ledgers_own_copy() -> None:
    deps, link, _warden_end = make_queen_deps()
    await set_ceilings(link, _ceilings(max_sub_bees=4), deps)

    await change_ceilings(link, _ceilings(max_sub_bees=8), deps)

    stored = deps.ledger.decisions.ceilings_for(link.warden_id)
    assert stored is not None
    assert stored.max_sub_bees == 8
    events = await deps.trail.query(TrailQuery())
    ceilings_events = [e for e in events if e.kind == "forage.ceilings_set"]
    assert len(ceilings_events) == 2
    assert ceilings_events[1].payload["revision"] == 1


async def test_change_ceilings_carries_the_old_and_new_values_on_the_trail() -> None:
    deps, link, _warden_end = make_queen_deps()
    await set_ceilings(link, _ceilings(max_sub_bees=4), deps)

    await change_ceilings(link, _ceilings(max_sub_bees=8), deps)

    events = await deps.trail.query(TrailQuery())
    (change_event,) = [e for e in events if e.kind == "forage.ceilings_set"][1:]
    assert change_event.payload["old_max_sub_bees"] == 4
    assert change_event.payload["new_max_sub_bees"] == 8
