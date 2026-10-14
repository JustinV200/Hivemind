"""Tests for hivemind.wardens.ticks.results when the Cell cannot be asked to check a claim (11.6a).

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/results.py (codingrules section 3), split by feature (14.2)
    from test_results.py. A Warden checks a sub-bee's claim on its own session. When that session
    belongs to a device that went away (a closed session, a request the device refused, a link that
    dropped) the check used to raise out of the Warden's tick and end its loop, with the task left
    RUNNING and nobody to report it. Now nothing is accepted, the task is reported FAILED with the
    error's class as the reason, the sub-bee's slot is freed, and the Warden carries on.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.results for the module under test.
    - hivemind.wardens.acceptance for the check that raises.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from builders.quarantine import make_grant_issued
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.cell import SessionClosedError
from hivemind.swarm.errors import DeviceRequestError, GatewayUnavailableError
from hivemind.wardens import DeviceDeps
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.errors import ConnectionLostError
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign, TaskOutcome, WorkerRole

_STOP_TIMEOUT_S = 5.0  # A hung stop fails the test instead of blocking the suite.


# Keeps the lease the Warden started with: an idle device Warden would lease afresh for the task,
# and the session these tests break would never be the one acceptance uses.
_KEEP_LEASE = DeviceDeps(release_lease_when_idle=False)


async def _writes_output(
    ctx: WorkerContext, assignment: TaskAssign, resume_from: object
) -> WorkerOutcome:
    """The role: write the file the acceptance wants, then claim the work done."""
    await asyncio.sleep(0)
    await ctx.session.put_file(Path("output.txt"), b"done")
    return make_outcome()


def _factory(role: WorkerRole) -> ScriptedWorker:
    return ScriptedWorker(_writes_output, role=role)


_ERRORS: list[Callable[[], Exception]] = [
    lambda: SessionClosedError(Path("/scratch")),
    lambda: DeviceRequestError("pollen.busy", "The device is busy."),
    lambda: GatewayUnavailableError("The device's link ended."),
    lambda: ConnectionLostError("The link dropped."),
]


@pytest.mark.parametrize("make_error", _ERRORS, ids=["closed", "refused", "gateway", "link"])
async def test_a_device_error_while_checking_fails_the_task_and_the_loop_survives(
    make_error: Callable[[], Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    deps, queen_end, warden_id = make_warden_deps(device=_KEEP_LEASE, worker_factory=_factory)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    session = warden._session
    assert session is not None
    original = session.get_file
    failing = [True]

    async def get_file(path: Path) -> bytes:
        if failing[0]:
            raise make_error()
        return await original(path)

    monkeypatch.setattr(session, "get_file", get_file)
    criterion = Postcondition(
        kind=PostconditionKind.FILE_EXISTS, subject="output.txt", argv=(), expected=None
    )
    first = make_assignment(clock=deps.clock, acceptance=(criterion,))
    await queen_end.send(make_grant_issued(deps.clock, first))
    await queen_end.send(first)

    result = await asyncio.wait_for(queen_end.wait_for_result(), timeout=_STOP_TIMEOUT_S)

    assert result.outcome is TaskOutcome.FAILED and result.checked_by == warden_id
    assert result.task_id == first.task_id
    assert result.reason == f"acceptance could not be checked: {type(make_error()).__name__}"
    assert not run_task.done()  # The loop is still going.
    assert warden._sub_bee_slots.in_use == 0 and warden.sub_bees == ()
    # And the Warden still works: the device answers again, and the next task is accepted.
    failing[0] = False
    second = make_assignment(clock=deps.clock, acceptance=(criterion,))
    await queen_end.send(make_grant_issued(deps.clock, second))
    await queen_end.send(second)
    second_result = queen_end.pump_until(lambda: len(queen_end.results) == 2)
    await asyncio.wait_for(second_result, timeout=_STOP_TIMEOUT_S)
    next_result = queen_end.results[-1]
    assert next_result.task_id == second.task_id
    assert next_result.outcome is TaskOutcome.SUCCEEDED
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=_STOP_TIMEOUT_S)
