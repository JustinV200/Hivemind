"""Tests for hivemind.honey_store.browse.fake: the in-memory wax and Bee Bread sources.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/browse/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.browse.fake for the module under test.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from unit.honey_store.browse.harness import open_browse_hive

from hivemind.cell import HoneyClearance
from hivemind.honey_store.browse.fake import FakeBeeBreadSource, FakeLiveWaxSource
from waggle.ids import new_cell_id


async def test_fake_wax_source_filters_by_cell_and_allowance_newest_first(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    cell, other = new_cell_id(hive.clock), new_cell_id(hive.clock)
    older = hive.wax_note(cell)
    hive.clock.advance(1)
    newer = hive.wax_note(cell)
    royal = hive.wax_note(cell, clearance=HoneyClearance.C2)
    elsewhere = hive.wax_note(other)
    source = FakeLiveWaxSource((older, newer, royal, elsewhere))

    one_cell = await source.live_wax(cell, HoneyClearance.C1)
    every_cell = await source.live_wax(None, HoneyClearance.C2)

    assert one_cell == (newer, older)
    assert set(every_cell) == {older, newer, royal, elsewhere}


async def test_fake_bee_bread_source_serves_a_window_and_lookups(tmp_path: Path) -> None:
    hive = await open_browse_hive(tmp_path)
    now = hive.clock.now()
    old = hive.bee_bread_note(created_at=now - timedelta(days=2))
    first = hive.bee_bread_note(created_at=now - timedelta(seconds=2))
    second = hive.bee_bread_note(created_at=now - timedelta(seconds=1))
    royal = hive.bee_bread_note(clearance=HoneyClearance.C2)
    source = FakeBeeBreadSource((old, first, second, royal))

    recent = await source.recent(now - timedelta(days=1), now, HoneyClearance.C1, 10)
    newest = await source.recent(now - timedelta(days=1), now, HoneyClearance.C1, 1)

    assert recent == (second, first)
    assert newest == (second,)
    assert await source.entry(old.id, HoneyClearance.C1) == old
    assert await source.entry(royal.id, HoneyClearance.C1) is None
    assert await source.entry("event_missing", HoneyClearance.C2) is None
