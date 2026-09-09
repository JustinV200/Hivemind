"""Tests for Warden: spawning a sub-bee (both TaskAssign/GrantIssued orders) and its acceptance.

Fits into the Hive:
    Mirrors src/hivemind/wardens/warden.py (codingrules section 3); split by feature (14.2) from
    test_warden_lifecycle.py, test_warden_alarms.py, test_warden_forwarding.py and
    test_warden_heartbeat.py. Exercises hivemind.wardens.ticks.assign and .results together with
    hivemind.wardens.acceptance, through the Warden's own queen link (roadmap steps 3.18, 3.19).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.warden, .ticks.assign, .ticks.results, .acceptance for the modules under
      test.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign, WorkerRole


def _grant(active_clock: Clock, grant_id: GrantId, *, max_sub_bees: int = 1) -> GrantIssued:
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
        max_sub_bees=max_sub_bees,
        expires_at=active_clock.now(),
        reason="test grant",
    )


def _file_writing_worker_factory(
    path: str, content: bytes = b"done"
) -> Callable[[WorkerRole], ScriptedWorker]:
    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        await asyncio.sleep(0)
        await ctx.session.put_file(Path(path), content)
        return make_outcome()

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


def _empty_claim_worker_factory() -> Callable[[WorkerRole], ScriptedWorker]:
    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        await asyncio.sleep(0)
        return make_outcome()

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


@pytest.mark.parametrize("assign_first", [True, False])
async def test_spawn_completes_and_reports_succeeded_with_checked_by(assign_first: bool) -> None:
    deps, queen_end, warden_id = make_warden_deps(
        worker_factory=_file_writing_worker_factory("output.txt")
    )
    assignment = make_assignment(
        clock=deps.clock,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="output.txt", argv=(), expected=None
            ),
        ),
    )
    grant = _grant(deps.clock, assignment.grant_id)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    if assign_first:
        await queen_end.send(assignment)
        await queen_end.send(grant)
    else:
        await queen_end.send(grant)
        await queen_end.send(assignment)
    result = await queen_end.wait_for_result()

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert result.outcome.value == "SUCCEEDED"
    assert result.checked_by == warden_id
    assert result.task_id == assignment.task_id


async def test_acceptance_failure_raises_an_alarm_and_reports_failed() -> None:
    deps, queen_end, warden_id = make_warden_deps(worker_factory=_empty_claim_worker_factory())
    assignment = make_assignment(
        clock=deps.clock,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS,
                subject="never-written.txt",
                argv=(),
                expected=None,
            ),
        ),
    )
    grant = _grant(deps.clock, assignment.grant_id)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await queen_end.send(assignment)
    await queen_end.send(grant)
    result = await queen_end.wait_for_result()
    alarm = await queen_end.wait_for_alarm()

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert result.outcome.value == "FAILED"
    assert result.reason == "acceptance"
    assert result.checked_by == warden_id
    assert alarm.kind.value == "ACCEPTANCE_FAILED"
    assert "FILE_EXISTS" in alarm.detail
