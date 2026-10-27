"""Tests for hivemind.queen.requeening.snapshot: the Queen's own periodic Handoff (roadmap 13.2).

A snapshot is written once per interval, its cursor trailing it by the margin and never moving
back, and none while a recovery is still settling. What makes recovery bounded is that the leases
are folded from the newest snapshot's cursor: an event older than the cursor is never read again,
so a lease the snapshot already closed stays closed whatever the trail before it says.

Fits into the Hive:
    Mirrors src/hivemind/queen/requeening/snapshot.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.requeening.snapshot for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

from builders.queen import make_queen_deps

from hivemind.cell import hive_stand_cell_id
from hivemind.pheromone import CellEvent
from hivemind.queen.deps import QueenDeps
from hivemind.queen.requeening import (
    OpenLease,
    PendingRecovery,
    QueenSnapshot,
    RequeenCounts,
    current_leases,
    take_snapshot_when_due,
)
from hivemind.queen.requeening.snapshot import CURSOR_MARGIN_S
from waggle.clock import FakeClock
from waggle.ids import new_event_id


async def test_a_snapshot_is_written_once_per_interval_and_never_while_recovery_settles() -> None:
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    book = deps.requeening

    first = await take_snapshot_when_due(deps, [link])
    too_soon = await take_snapshot_when_due(deps, [link])
    clock.advance(book.snapshot_interval_s)
    book.pending = PendingRecovery(clock.now(), frozenset(), RequeenCounts(), None)
    settling = await take_snapshot_when_due(deps, [link])
    book.pending = None
    second = await take_snapshot_when_due(deps, [link])

    assert (too_soon, settling) == (None, None)
    assert first is not None and second is not None
    assert first.cursor == first.taken_at - timedelta(seconds=CURSOR_MARGIN_S)
    assert second.cursor > first.cursor and second.wardens == (link.warden_id,)
    assert await book.store.latest_snapshot() == second


async def test_leases_are_folded_from_the_snapshots_cursor_and_no_earlier() -> None:
    clock = FakeClock()
    deps, _link, _end = make_queen_deps(clock)
    cell = hive_stand_cell_id(deps.identity.node_id)
    # A lease opened before the snapshot's cursor, which the snapshot says is no longer open:
    # the snapshot is the word on it, so this event is never read again.
    await deps.trail.record(_leased(deps, "lease_01ARZ3NDEKTSV4RRFFQ69G5FA0"))
    clock.advance(10.0)
    kept = OpenLease(
        lease_id="lease_01ARZ3NDEKTSV4RRFFQ69G5FA1",
        cell_id=cell,
        holder="warden_x",
        task_id=None,
        leased_at=clock.now(),
    )
    snapshot = QueenSnapshot(
        taken_at=clock.now(), cursor=clock.now(), open_leases=(kept,), wardens=()
    )

    open_now = await current_leases(deps, snapshot)

    assert [lease.lease_id for lease in open_now] == [kept.lease_id]


def _leased(deps: QueenDeps, lease_id: str) -> CellEvent:
    """A `cell.leased` her own node recorded on the Hive Stand's Cell, now."""
    clock, identity = deps.clock, deps.identity
    return CellEvent(
        id=new_event_id(clock),
        hive_id=identity.hive_id,
        node_id=identity.node_id,
        at=clock.now(),
        actor="system",
        kind="cell.leased",
        subject_id=hive_stand_cell_id(identity.node_id),
        payload={"lease_id": lease_id, "holder": "warden_old"},
    )
