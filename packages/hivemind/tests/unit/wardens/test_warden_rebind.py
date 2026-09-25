"""Tests for a Warden's rebinds at the slot_binding point (roadmap step 10.3, ADR-0039).

Every rebind stays inside the grant's `allowed` bindings and the sub-bee's own `llm:<slot>`: a
Queen-sent `Intervene(REBIND)` (`hivemind.wardens.ticks.alarms.rebind_sub_bee`, reached through
`hivemind.wardens.ticks.control`) is now checked like the Warden's own, one that lands records
`llm.rebound`, and one the Guard refuses is a `guard.denied` row and a FAILED result for the task.

Fits into the Hive:
    Mirrors src/hivemind/wardens/ticks/alarms.py (codingrules section 3); split by feature (14.2)
    from test_warden_alarms.py, like its other test_warden_* siblings.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.spawn.binding for authorize_binding, the check every rebind passes.
"""

from __future__ import annotations

import asyncio

from builders.wardens import QueenEnd, make_warden_deps
from builders.workers import ScriptedWorker, make_assignment

from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.spawn import SubBee
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import Clock
from waggle.ids import GrantId, new_cell_id, new_warden_id
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.supervision import Intervene, InterventionAction
from waggle.messages.task import TaskAssign, TaskOutcome, WorkerRole


def _worker_grant(clock: Clock, grant_id: GrantId) -> GrantIssued:
    """A grant naming only the WORKER slot, so the JUDGE slot lies outside it."""
    return GrantIssued(
        grant_id=grant_id,
        holder=new_warden_id(clock),
        cell_id=new_cell_id(clock),
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
        max_sub_bees=2,
        expires_at=clock.now(),
        reason="test grant",
    )


def _never_finishing(role: WorkerRole) -> ScriptedWorker:
    """A sub-bee that works until it is retired, so a rebind always finds it running."""

    async def script(ctx: WorkerContext, assignment: TaskAssign, resume: object) -> WorkerOutcome:
        await asyncio.Event().wait()
        raise AssertionError("unreachable: the Warden retires this sub-bee first")

    return ScriptedWorker(script, role=role)


async def _running(warden: Warden, queen_end: QueenEnd, clock: Clock) -> SubBee:
    """Assign the running `warden` one task under `_worker_grant`, and return its sub-bee."""
    assignment = make_assignment(clock=clock)
    await queen_end.send(assignment)
    await queen_end.send(_worker_grant(clock, assignment.grant_id))
    for _ in range(500):
        if warden.sub_bees:
            return warden.sub_bees[0]
        await asyncio.sleep(0)
    raise AssertionError("the Warden never spawned its sub-bee")


def _rebind(sub_bee: SubBee, binding: str) -> Intervene:
    """The Queen's own REBIND order for `sub_bee`, naming the `[llm.slots]` key to move to."""
    return Intervene(
        action=InterventionAction.REBIND,
        subject=sub_bee.worker_id,
        task_id=sub_bee.task_id,
        slot="WORKER",
        binding=binding,
        alarm_id=None,
        reason="the provider went down",
    )


async def _first(deps: WardenDeps, kind: str) -> PheromoneEvent:
    """Yield until an event of `kind` is on the Warden's trail, and return it."""
    for _ in range(500):
        found = await deps.trail.query(TrailQuery(kind=kind))
        if found:
            return found[0]
        await asyncio.sleep(0)
    raise AssertionError(f"no {kind} was ever recorded")


async def test_a_queen_ordered_rebind_inside_the_grant_records_llm_rebound() -> None:
    deps, queen_end, warden_id = make_warden_deps(worker_factory=_never_finishing)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    sub_bee = await _running(warden, queen_end, deps.clock)

    # `worker_fallback` is a named binding on the WORKER slot's own chain (the builder's rows).
    await queen_end.send(_rebind(sub_bee, "worker_fallback"))
    rebound = await _first(deps, "llm.rebound")

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    assert rebound.payload["from_binding"] == "worker"
    assert rebound.payload["to_binding"] == "worker_fallback"
    assert rebound.payload["ordered_by"] == "queen"
    assert rebound.payload["task_id"] == sub_bee.task_id
    assert await deps.trail.query(TrailQuery(kind="guard.denied")) == ()


async def test_a_queen_ordered_rebind_outside_the_grant_is_refused_and_fails_the_task() -> None:
    deps, queen_end, warden_id = make_warden_deps(worker_factory=_never_finishing)
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    sub_bee = await _running(warden, queen_end, deps.clock)

    await queen_end.send(_rebind(sub_bee, "judge"))  # The grant names no JUDGE binding.
    result = await queen_end.wait_for_result()

    await warden.stop()
    await asyncio.wait_for(run_task, timeout=5.0)
    assert result.task_id == sub_bee.task_id
    assert result.outcome is TaskOutcome.FAILED
    [denial] = await deps.trail.query(TrailQuery(kind="guard.denied"))
    assert denial.payload["point"] == "slot_binding"
    assert denial.payload["principal_kind"] == "queen"
    assert denial.payload["capability"] == "llm:judge"
    assert await deps.trail.query(TrailQuery(kind="llm.rebound")) == ()
