"""Tests for hivemind.queen.requeening.leases: which Hive Stand leases are open, and who gets them.

The fold must leave open exactly the leases opened and not released, however the events are cut
across a snapshot (an event read twice changes nothing). The trail read must take only the
Queen's own node's events about the Hive Stand's Cell, so a merged segment can never name a lease
for her to give back; and a lease is given back only when its holder is not attached, with the
paths it touched outside scratch counted.

Fits into the Hive:
    Mirrors src/hivemind/queen/requeening/leases.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.requeening.leases for the module under test.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from builders.queen import make_queen_deps

from hivemind.cell import OrphanedLease, hive_stand_cell_id
from hivemind.pheromone import CellEvent, PheromoneEvent
from hivemind.queen.deps import QueenDeps
from hivemind.queen.requeening import OpenLease, fold_leases, release_orphans
from hivemind.queen.requeening.leases import lease_events
from waggle.clock import FakeClock
from waggle.ids import CellId, NodeId, WardenId, new_event_id, new_node_id

_CLOCK = FakeClock()


def _event(
    deps: QueenDeps, kind: str, payload: dict[str, str], node: NodeId | None = None
) -> CellEvent:
    """One event about the Hive Stand's Cell, a second after the last, on `node` (else hers)."""
    _CLOCK.advance(1.0)
    return CellEvent(
        id=new_event_id(_CLOCK),
        hive_id=deps.identity.hive_id,
        node_id=node if node is not None else deps.identity.node_id,
        at=_CLOCK.now(),
        actor="system",
        kind=kind,
        subject_id=_cell(deps),
        payload={"holder": "warden_old", **payload},
    )


class _Releaser:
    """A HiveStandLeases that keeps what it was handed and gives every lease back."""

    def __init__(self) -> None:
        self.released: list[OrphanedLease] = []

    async def release_orphan(self, orphan: OrphanedLease) -> bool:
        """Keep `orphan`."""
        self.released.append(orphan)
        return True


@pytest.mark.parametrize("cut", [0, 1, 2, 3])
def test_the_fold_leaves_open_what_was_leased_and_not_released_however_it_is_cut(
    cut: int,
) -> None:
    deps, _link, _end = make_queen_deps(_CLOCK)
    events: list[PheromoneEvent] = [
        _event(deps, "cell.leased", {"lease_id": "lease_a"}),
        _event(deps, "cell.released", {"lease_id": "lease_a"}),
        _event(deps, "cell.leased", {"lease_id": "lease_b"}),
    ]

    snapshot = fold_leases((), events[:cut])
    # The snapshot's cursor overlaps: the event at it is read again on top of the fold.
    folded = fold_leases(snapshot, events[max(cut - 1, 0) :])

    assert [lease.lease_id for lease in folded] == ["lease_b"]


async def test_only_her_own_nodes_events_about_the_hive_stand_are_read() -> None:
    deps, _link, _end = make_queen_deps(_CLOCK)
    await deps.trail.record(_event(deps, "cell.leased", {"lease_id": "lease_ours"}))
    # A merged segment from another node naming her Cell: never a lease she gives back.
    forged = _event(deps, "cell.leased", {"lease_id": "lease_forged"}, node=new_node_id(_CLOCK))
    await deps.trail.record(forged)

    read = await lease_events(deps, _cell(deps), None)

    assert [event.payload["lease_id"] for event in read] == ["lease_ours"]


async def test_only_a_gone_holders_lease_is_given_back_with_its_touches_counted() -> None:
    deps, _link, _end = make_queen_deps(_CLOCK)
    touch = {"lease_id": "lease_gone", "path": "/etc/hosts"}
    await deps.trail.record(_event(deps, "cell.touched_outside_scratch", touch))
    leases = [_lease(deps, "lease_gone", "warden_gone"), _lease(deps, "lease_live", "warden_live")]
    releaser = _Releaser()

    released = await release_orphans(deps, leases, {WardenId("warden_live")}, releaser)

    assert released == 1
    assert [(o.lease_id, o.touched_outside) for o in releaser.released] == [("lease_gone", 1)]


def _lease(deps: QueenDeps, lease_id: str, holder: str) -> OpenLease:
    """An open lease taken an hour ago, so every touch in the test is after it."""
    leased_at = _CLOCK.now() - timedelta(hours=1)
    return OpenLease(
        lease_id=lease_id, cell_id=_cell(deps), holder=holder, task_id=None, leased_at=leased_at
    )


def _cell(deps: QueenDeps) -> CellId:
    """The Hive Stand's Cell for `deps`'s node."""
    return hive_stand_cell_id(deps.identity.node_id)
