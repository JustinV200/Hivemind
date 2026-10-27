"""Tests for hivemind.queen.requeening.recover: the two halves of Requeening.

A restarted Queen takes back at once a task whose Warden died with her (the Hive Stand's, or a
Virtual Cell's that is gone) and gives back the Hive Stand lease her dead process left open, but
never touches a Swarm device's task, which the swarm gate holds. A task on a Virtual Cell her
backends still list waits for its Warden: if it redials inside the grace nothing is taken from
it; if not, its task is taken back once the grace is over. Either way `queen.requeened` is
recorded once, counts only, and she ends RUNNING.

Fits into the Hive:
    Mirrors src/hivemind/queen/requeening/recover.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.requeening.recover for the module under test.
"""

from __future__ import annotations

import pytest
from builders.forage import make_grant
from builders.queen import make_queen_deps, make_warden_link
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import CombShieldLevel, OrphanedLease, hive_stand_cell_id
from hivemind.forage.grant_state import GrantState
from hivemind.hive import VirtualCellRecord, VirtualCellStatus
from hivemind.pheromone import CellEvent, TrailQuery
from hivemind.queen.deps import QueenDeps
from hivemind.queen.queen import Queen
from hivemind.queen.requeening import RequeeningStart, begin_requeening, settle_requeening
from hivemind.queen.state import QueenMode
from waggle.clock import FakeClock
from waggle.ids import (
    CellId,
    HiveId,
    WardenId,
    new_cell_id,
    new_event_id,
    new_task_id,
    new_warden_id,
)


class _Releaser:
    """A HiveStandLeases that keeps what it was handed and gives every lease back."""

    def __init__(self) -> None:
        self.released: list[OrphanedLease] = []

    async def release_orphan(self, orphan: OrphanedLease) -> bool:
        """Keep `orphan`."""
        self.released.append(orphan)
        return True


async def _restarted(clock: FakeClock) -> tuple[Queen, Task, WardenId]:
    """A Queen just restarted, her own Hive Stand Warden attached, and a task her dead self left.

    The task is RUNNING on a Warden that is not attached, on a Cell of its own; the dead process's
    Hive Stand lease is still open on the trail.
    """
    deps, link, _end = make_queen_deps(clock)
    queen = Queen(deps)
    await queen.attach_warden(link)
    (task,) = await deps.chamber.submit(make_graph_draft({"poem": ()}))
    old: WardenId = new_warden_id(clock)
    cell = new_cell_id(clock)
    await deps.chamber.assign(task.id, old, cell, "Placed.", bound_tier=CombShieldLevel.MEADOW)
    await deps.chamber.start(task.id)
    await deps.trail.record(_leased(deps, old))
    return queen, await deps.chamber.get(task.id), old


def _leased(deps: QueenDeps, holder: WardenId) -> CellEvent:
    """The `cell.leased` the dead process's Hive Stand Warden recorded."""
    return CellEvent(
        id=new_event_id(deps.clock),
        hive_id=deps.identity.hive_id,
        node_id=deps.identity.node_id,
        at=deps.clock.now(),
        actor=holder,
        kind="cell.leased",
        subject_id=hive_stand_cell_id(deps.identity.node_id),
        payload={"lease_id": "lease_01ARZ3NDEKTSV4RRFFQ69G5FAV", "holder": holder},
    )


def _listed(cell_id: CellId, clock: FakeClock) -> VirtualCellRecord:
    """A Virtual Cell her backends still list."""
    return VirtualCellRecord(
        cell_id=cell_id,
        status=VirtualCellStatus.READY,
        image="base-ubuntu",
        labels={},
        created_at=clock.now(),
    )


async def _requeened(deps: QueenDeps) -> list[dict[str, object]]:
    """Every `queen.requeened` payload on her trail."""
    events = await deps.trail.query(TrailQuery(kind="queen.requeened"))
    return [dict(event.payload) for event in events]


@pytest.mark.parametrize(("on_a_device", "given_back"), [(False, 1), (True, 0)])
async def test_a_dead_wardens_task_is_taken_back_at_once_unless_a_device_holds_it(
    on_a_device: bool, given_back: int
) -> None:
    clock = FakeClock()
    queen, task, _old = await _restarted(clock)
    deps, releaser = queen._deps, _Releaser()
    devices = frozenset({task.cell_id}) if on_a_device and task.cell_id else frozenset()

    await begin_requeening(queen, RequeeningStart(device_cells=devices, hive_stand=releaser))

    after = await deps.chamber.get(task.id)
    assert after.status is (TaskStatus.RUNNING if on_a_device else TaskStatus.PENDING)
    [payload] = await _requeened(deps)  # Nobody to wait for: settled at once.
    assert (payload["given_back"], payload["leases_released"]) == (given_back, 1)
    assert set(map(type, payload.values())) <= {int, type(None)}  # Counts only: no ids.
    assert deps.cluster_state.mode is QueenMode.RUNNING
    assert [orphan.lease_id for orphan in releaser.released] == ["lease_01ARZ3NDEKTSV4RRFFQ69G5FAV"]


@pytest.mark.parametrize("comes_back", [True, False])
async def test_a_virtual_cells_task_waits_for_its_warden_until_the_grace_is_over(
    comes_back: bool,
) -> None:
    clock = FakeClock()
    queen, task, old = await _restarted(clock)
    deps = queen._deps
    assert task.cell_id is not None
    start = RequeeningStart(virtual_cells=(_listed(task.cell_id, clock),))

    await begin_requeening(queen, start)
    waiting = await settle_requeening(deps, queen.wardens)
    if comes_back:
        hop = queen.wardens[0].hop
        link, _end = make_warden_link(
            HiveId(hop.sender), old, hop.node_id, queen.wardens[0].cell, clock
        )
        await queen.attach_warden(link)
    else:
        clock.advance(deps.requeening.grace_s + 1.0)
    counts = await settle_requeening(deps, queen.wardens)

    assert waiting is None  # Inside the grace, with its Warden yet to redial.
    assert deps.cluster_state.mode is QueenMode.RUNNING
    assert counts is not None
    assert (counts.wardens_back, counts.wardens_missing) == ((1, 0) if comes_back else (0, 1))
    after = await deps.chamber.get(task.id)
    assert after.status is (TaskStatus.RUNNING if comes_back else TaskStatus.PENDING)
    assert len(await _requeened(deps)) == 1


async def test_a_grant_whose_task_ended_is_released_and_one_for_an_unknown_task_is_left() -> None:
    # Regression: the ended tasks were found by a capped, oldest-first list of the whole chamber,
    # which on an old Hive missed the newest ones; now each live grant's own task is read.
    clock = FakeClock()
    deps, link, _end = make_queen_deps(clock)
    queen = Queen(deps)
    await queen.attach_warden(link)
    (task,) = await deps.chamber.submit(make_graph_draft({"poem": ()}))
    await deps.chamber.cancel(task.id, "Dropped before the crash.")
    ended = make_grant(clock=clock, state=GrantState.ACTIVE, task_id=task.id)
    unknown = make_grant(clock=clock, state=GrantState.ACTIVE, task_id=new_task_id(clock))
    for grant in (ended, unknown):
        await deps.ledger.record_grant(grant)

    await begin_requeening(queen, RequeeningStart())

    [payload] = await _requeened(deps)
    assert payload["grants_released"] == 1
    assert [grant.id for grant in deps.ledger.live_grants()] == [unknown.id]
