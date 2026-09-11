"""Tests for hivemind.wardens.warden.Warden: start, stop, and the trail they leave.

Fits into the Hive:
    Mirrors src/hivemind/wardens/warden.py (codingrules section 3); split by feature (14.2) from
    test_warden_spawn_and_accept.py, test_warden_alarms.py, test_warden_forwarding.py and
    test_warden_heartbeat.py.
    `test_stop_tolerates_a_heartbeat_that_raced_an_already_closed_queen_link` is this dispatch's
    own fix 4's proof: `Warden.stop()` and `hivemind.wardens.ticks.heartbeat.send_heartbeat`
    together keep `stop()` from ever raising `waggle.errors.TransportClosedError` when a
    heartbeat races `cli.compose.hive.run_hive`'s own teardown closing the Queen link.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.warden for the module under test.
"""

from __future__ import annotations

import asyncio
from typing import cast

from builders.cells import make_cell
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import CellKind
from hivemind.cell.lease import LeaseRequest
from hivemind.cell.tiers import AccessLevel
from hivemind.pheromone.trail import TrailQuery
from hivemind.wardens.state import WardenState
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock, FakeClock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.task import TaskAssign, WorkerRole


def _grant(active_clock: Clock, grant_id: GrantId) -> GrantIssued:
    """Build a minimal GrantIssued for one sub-bee's own spawn, matching test_spawn.py's own."""
    return GrantIssued(
        grant_id=grant_id,
        holder=new_warden_id(active_clock),
        cell_id=new_cell_id(active_clock),
        task_id=None,
        revision=0,
        allowed=(
            AllowedBinding(
                slot="WORKER",
                source=SourceRef(
                    source_id="local", provider="fake", model="test-model", host_cell_id=None
                ),
                max_effort=WireEffort.MEDIUM,
            ),
        ),
        seats=(),
        token_budget=500_000,
        spend_budget=5.0,
        tokens_spent=0,
        spent=0.0,
        max_sub_bees=1,
        expires_at=active_clock.now(),
        reason="test grant",
    )


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


async def test_stop_leaves_no_pending_tasks_behind_a_still_running_sub_bee() -> None:
    """This dispatch's own shutdown-hygiene proof: stop() reaps every task, its sub-bee's too.

    Before this fix, `stop()` cancelled `sub_bee.runtime_task` without awaiting it, and dropped
    this Warden's own receive task for the sub-bee's link with a bare `dict.pop`: both were left
    pending, exactly the `asyncio` "Task was destroyed but it is pending!" warnings the e2e
    suite's own live log showed for this class before the fix.
    """
    started = asyncio.Event()
    forever = asyncio.Event()

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        started.set()
        await forever.wait()  # Never set; this attempt only ends by being stopped.
        return make_outcome()  # pragma: no cover

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    deps, queen_end, warden_id = make_warden_deps(worker_factory=factory)
    assignment = make_assignment(clock=deps.clock)
    grant = _grant(deps.clock, assignment.grant_id)
    warden = Warden(warden_id, deps)
    await warden.start()
    before = asyncio.all_tasks() - {asyncio.current_task()}
    run_task = asyncio.ensure_future(warden.run())

    await queen_end.send(grant)
    await queen_end.send(assignment)
    await started.wait()
    assert warden.sub_bees  # Sanity: the sub-bee is still attached when stop() runs below.

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    after = asyncio.all_tasks() - {asyncio.current_task()}
    assert after == before


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


async def _settle(cycles: int = 20) -> None:
    """Give the background run() task several event-loop turns to react to a clock advance."""
    for _ in range(cycles):
        await asyncio.sleep(0)


async def test_stop_tolerates_a_heartbeat_that_raced_an_already_closed_queen_link() -> None:
    """Fix 4: close the queen link, then stop the Warden with a heartbeat due.

    No exception out of stop(), and warden.stopped is still recorded. The Queen's own end closes
    first (exactly what `cli.compose.hive.run_hive`'s own teardown does right after `Warden.
    stop()` returns, from a separate task -- deliberately raced here instead); the clock advance
    that follows lets the run() task's own tick actually attempt the send and find the closed
    transport, tolerating it (`hivemind.wardens.ticks.heartbeat.send_heartbeat`) before `stop()`
    is even called, so this proves the send-side half of the fix specifically (the other half,
    `stop()` cancelling a heartbeat that has not fired yet, is already exercised by
    `test_stop_ends_a_concurrently_running_run_loop` above).
    """
    deps, queen_end, warden_id = make_warden_deps(heartbeat_interval_s=1.0)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    await _settle()  # let the first tick schedule its own heartbeat deadline

    await queen_end.close()
    cast(FakeClock, deps.clock).advance(deps.heartbeat_interval_s + 0.1)  # a heartbeat is due
    await _settle()  # let the run() task's own tick attempt (and tolerate) the send

    await warden.stop()  # must not raise, whichever path above the send-tolerance came through
    await asyncio.wait_for(run_task, timeout=5.0)

    events = await deps.trail.query(TrailQuery(subject_id=warden_id))
    assert "warden.stopped" in [event.kind for event in events]
