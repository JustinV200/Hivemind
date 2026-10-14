"""Tests for hivemind.wardens.spawn.spawn.stop_sub_bee: a bee's own death never ends its Warden.

A sub-bee's runtime is a task its Warden owns and reaps when it retires the bee. When the runtime
dies of an error it did not handle (here, a trail that cannot record `worker.started`), the error
is stored in that task until the reap, and the reap used to raise it into whatever was retiring
the bee: the Queen's fresh assignment superseding it, or the Warden stopping. A Cell's Warden
exited that way when an isolation's pause met a call that failed. The runtime now ends with one
typed `WorkerRuntimeError`, which `stop_sub_bee` reads, logs and lets go.

Fits into the Hive:
    Mirrors src/hivemind/wardens/spawn/spawn.py (codingrules section 3), split by feature from
    test_spawn.py; the whole-Warden case is test_warden_runtime_death.py's.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.spawn for the module under test.
    - tests.unit.workers.runtime.test_loop_paused for the runtime ending as a typed error.
"""

from __future__ import annotations

import asyncio

from builders.cells import make_cell
from builders.quarantine import make_grant_issued, settle
from builders.trail_faults import FailingTrail
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment
from structlog.testing import capture_logs

from hivemind.cell import CellKind
from hivemind.cell.lease import LeaseRequest
from hivemind.guard import warden_set
from hivemind.wardens.spawn import WardenCellContext, spawn_sub_bee, stop_sub_bee
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import FakeClock
from waggle.messages.task import TaskAssign, WorkerRole

_HUNG_S = 1_000_000.0  # A role that never finishes on its own within any test here.


def _hanging_worker(role: WorkerRole) -> ScriptedWorker:
    """Build a role that waits for ever, so only its runtime's own death ends the bee."""

    async def script(ctx: WorkerContext, _assign: TaskAssign, _resume: object) -> WorkerOutcome:
        await ctx.clock.sleep(_HUNG_S)
        raise AssertionError("a hung role never wakes during these tests")

    return ScriptedWorker(script, role=role)


async def test_stop_sub_bee_reads_logs_and_lets_go_of_the_error_a_dead_runtime_left() -> None:
    clock = FakeClock()
    trail = FailingTrail(clock, failing_kind="worker.started")
    cell = make_cell(kind=CellKind.REAL)
    deps, _queen_end, warden_id = make_warden_deps(
        clock, cells=(cell,), trail=trail, worker_factory=_hanging_worker
    )
    lease = await deps.source.lease(
        LeaseRequest(
            cell_id=cell.id, holder=warden_id, task_id=None, access_level=cell.access_level
        )
    )
    session = await deps.source.open_session(lease)
    ceiling = warden_set(deps.guard, lease.access_level, lease.scratch_root)
    assignment = make_assignment(clock=clock)
    ctx = WardenCellContext(
        warden_id=warden_id, deps=deps, ceiling=ceiling, cell=cell, lease=lease, session=session
    )
    sub_bee = await spawn_sub_bee(ctx, assignment, make_grant_issued(clock, assignment))
    # Its runtime meets the trail that cannot record `worker.started` and dies at once.
    await settle(lambda: sub_bee.runtime_task.done())
    assert trail.failures == 1

    with capture_logs() as logs:
        await stop_sub_bee(sub_bee, clock)  # Returns: nothing is raised into the Warden.

    [seen] = [entry for entry in logs if entry["event"] == "wardens.sub_bee.runtime_failed"]
    assert seen["worker_id"] == sub_bee.worker_id and seen["task_id"] == assignment.task_id
    assert seen["error"] == "OSError"  # The class of the cause, never its text.
    await sub_bee.link.close()
    await asyncio.sleep(0)  # Let the closed pair's last callbacks run before the loop ends.
