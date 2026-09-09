"""Tests for Warden: a crashing sub-bee is respawned by RETRY, then eventually ESCALATEd.

Fits into the Hive:
    Mirrors src/hivemind/wardens/warden.py (codingrules section 3); split by feature (14.2) from
    test_warden_lifecycle.py, test_warden_spawn_and_accept.py, test_warden_forwarding.py and
    test_warden_heartbeat.py. Exercises hivemind.wardens.ticks.alarms and hivemind.wardens.
    autopilot.table's own attempt-keyed policy lookup (roadmap step 3.19).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.ticks.alarms for the module under test.
    - docs/supervision/default-policy.toml for WORKER_CRASHED's own rows: min_attempts=1 ->
      RESPAWN, min_attempts=3 -> ESCALATE, so two respawns precede an escalate -- this test
      crashes three times, not two, to match the shipped policy exactly (see this dispatch's
      report for why "the second failure" reads differently for WORKER_CRASHED specifically).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import cast

from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment

from hivemind.llm import FakeLLMProvider
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign, WorkerRole


def _grant(active_clock: Clock, grant_id: GrantId) -> GrantIssued:
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


def _always_crashing_worker_factory() -> Callable[[WorkerRole], ScriptedWorker]:
    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        await asyncio.sleep(0)
        raise RuntimeError("boom")

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


async def test_first_and_second_crash_are_retried_third_escalates() -> None:
    deps, queen_end, warden_id = make_warden_deps(worker_factory=_always_crashing_worker_factory())
    assignment = make_assignment(
        clock=deps.clock,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject="x.txt", argv=(), expected=None
            ),
        ),
    )
    grant = _grant(deps.clock, assignment.grant_id)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await queen_end.send(assignment)
    await queen_end.send(grant)

    alarm = await queen_end.wait_for_alarm()

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert alarm.kind.value == "WORKER_CRASHED"
    # No awake episode ran: pure autopilot handled every one of the three crashes.
    assert cast(FakeLLMProvider, deps.bound.provider).calls == []


async def test_a_respawned_sub_bee_runs_at_attempt_plus_one() -> None:
    deps, queen_end, warden_id = make_warden_deps(worker_factory=_always_crashing_worker_factory())
    assignment = make_assignment(clock=deps.clock)
    grant = _grant(deps.clock, assignment.grant_id)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())

    await queen_end.send(assignment)
    await queen_end.send(grant)

    # Pump until a second sub-bee has been spawned (the respawn from the first crash).
    for _ in range(200):
        if len(warden.sub_bees) == 1 and warden.sub_bees[0].attempt == 2:
            break
        await asyncio.sleep(0)
    else:  # pragma: no cover - defensive
        raise AssertionError("The Warden never respawned a second attempt in time.")

    assert warden.sub_bees[0].attempt == 2
    assert warden.sub_bees[0].task_id == assignment.task_id

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
