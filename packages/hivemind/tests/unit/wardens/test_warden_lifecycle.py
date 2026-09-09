"""Tests for hivemind.wardens.warden.Warden: start, stop, and the trail they leave.

Fits into the Hive:
    Mirrors src/hivemind/wardens/warden.py (codingrules section 3); split by feature (14.2) from
    test_warden_spawn_and_accept.py, test_warden_alarms.py, test_warden_forwarding.py and
    test_warden_heartbeat.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.warden for the module under test.
"""

from __future__ import annotations

import asyncio

from builders.cells import make_cell
from builders.wardens import make_warden_deps

from hivemind.cell import CellKind
from hivemind.cell.lease import LeaseRequest
from hivemind.cell.tiers import AccessLevel
from hivemind.pheromone.trail import TrailQuery
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from waggle.ids import new_warden_id


async def test_start_leases_its_cell_and_moves_to_active() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)

    await warden.start()

    assert warden.state is WardenState.ACTIVE
    assert warden.lease is not None


async def test_start_moves_to_watch_when_the_source_has_no_cell_at_all() -> None:
    deps, _queen_end, warden_id = make_warden_deps(cells=())
    warden = Warden(warden_id, deps)

    await warden.start()

    assert warden.state is WardenState.WATCH
    assert warden.lease is None


async def test_start_moves_to_watch_when_the_lease_is_refused() -> None:
    cell = make_cell(kind=CellKind.REAL)
    deps, _queen_end, warden_id = make_warden_deps(cells=(cell,))
    # Occupy the Warden's only Cell first (as a different holder), so its own start() hits
    # LeaseRefusedError for real rather than the "no cell at all" path.
    other_holder = new_warden_id(deps.clock)
    await deps.source.lease(
        LeaseRequest(
            cell_id=cell.id, holder=other_holder, task_id=None, access_level=AccessLevel.SCRATCH
        )
    )
    warden = Warden(warden_id, deps)

    await warden.start()

    assert warden.state is WardenState.WATCH
    assert warden.lease is None


async def test_stop_releases_the_lease_and_moves_to_stopped() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    lease = warden.lease
    assert lease is not None

    await warden.stop()

    assert warden.state is WardenState.STOPPED
    assert lease.state.value == "RELEASED"


async def test_stop_ends_a_concurrently_running_run_loop() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await warden.stop()

    await asyncio.wait_for(run_task, timeout=5.0)
    assert warden.state is WardenState.STOPPED


async def test_trail_shows_started_then_active_then_stopped_in_order() -> None:
    deps, _queen_end, warden_id = make_warden_deps()
    warden = Warden(warden_id, deps)

    await warden.start()
    await warden.stop()

    events = await deps.trail.query(TrailQuery(subject_id=warden_id))
    assert [event.kind for event in events] == ["warden.started", "warden.active", "warden.stopped"]


async def test_trail_shows_watch_then_stopped_when_the_lease_is_never_available() -> None:
    deps, _queen_end, warden_id = make_warden_deps(cells=())
    warden = Warden(warden_id, deps)

    await warden.start()
    await warden.stop()

    events = await deps.trail.query(TrailQuery(subject_id=warden_id))
    assert [event.kind for event in events] == ["warden.watch", "warden.stopped"]
