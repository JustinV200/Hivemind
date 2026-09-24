"""Tests for hivemind.wardens.ticks.trail_ship: ship the trail segment before a TaskResult."""

from __future__ import annotations

from builders.wardens import make_warden_deps

from hivemind.wardens.ticks.trail_ship import ship_trail_before_result
from hivemind.wardens.warden import Warden


class _RecordingTrailSync:
    """A `TrailSync` that counts its own syncs and can report a link already gone."""

    def __init__(self, *, ok: bool = True) -> None:
        self.syncs = 0
        self._ok = ok

    async def sync(self) -> bool:
        self.syncs += 1
        return self._ok


async def test_ships_once_through_the_wardens_own_trail_sync() -> None:
    sync = _RecordingTrailSync()
    deps, _queen_end, warden_id = make_warden_deps(trail_sync=sync)
    warden = Warden(warden_id, deps)

    await ship_trail_before_result(warden)

    assert sync.syncs == 1


async def test_a_warden_without_a_trail_sync_is_a_no_op() -> None:
    """The Hive Stand's own Warden: its trail already is the Queen's own store."""
    deps, _queen_end, warden_id = make_warden_deps(trail_sync=None)
    warden = Warden(warden_id, deps)

    await ship_trail_before_result(warden)  # Must not raise.


async def test_a_closed_link_is_reported_as_false_not_raised() -> None:
    """`TrailSync.sync` reports a closed or dropped link as False; this function must not raise."""
    sync = _RecordingTrailSync(ok=False)
    deps, _queen_end, warden_id = make_warden_deps(trail_sync=sync)
    warden = Warden(warden_id, deps)

    await ship_trail_before_result(warden)  # Must not raise.

    assert sync.syncs == 1
