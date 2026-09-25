"""Unit tests for hivemind.queen.dispatcher.ready.assign: the role and recon a TaskAssign carries.

Roadmap steps 6.9/6.10: `_recon_for` and `_task_assign` are exercised directly here (the footprint
fallback's `_grant_inputs` now lives in `sizing`, tested beside it), the same way
`hivemind.wardens.ticks.test_results` tests `_gui_check` directly, because the behaviour worth
pinning down (exact ordering, exact truncation, exact fallback) is easiest to see one function at a
time; `test_queen_dispatch.py` and `test_queen_results.py` cover the same fields end to end, through
a real `Queen` tick.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ready/assign.py (codingrules section 3); the ready
    path's first dedicated unit test file (it previously had none, only `test_queen_dispatch.py`'s
    end-to-end coverage).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.ready.assign for the module under test.
    - hivemind.wardens.ticks.test_results for the precedent of testing a mirrored module's own
      private helper directly.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from builders.queen import make_queen_deps
from builders.tasks import make_outcome, make_task, make_task_spec

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, MemoryTaskStore, TaskStatus
from hivemind.forage import RoleFootprint
from hivemind.pheromone import TaskEvent
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen.dispatcher.ready.assign import AssignmentTerms, _recon_for, _task_assign
from waggle.clock import FakeClock
from waggle.ids import (
    TaskId,
    new_cell_id,
    new_event_id,
    new_grant_id,
    new_hive_id,
    new_node_id,
    new_task_id,
)
from waggle.messages.task import ScoutReport, WorkerRole
from waggle.messages.task.recon import MAX_RECON_REPORTS

_START = datetime(2020, 1, 1, tzinfo=UTC)


def _footprint(memory_bytes: int) -> RoleFootprint:
    return RoleFootprint(
        cpu_cores=1.0, memory_bytes=memory_bytes, seats=1, token_rate_per_minute=1_000.0
    )


# ──────────────────────────────────────────────────────────────────────────────
# _recon_for
# ──────────────────────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True, slots=True)
class _DependencySpec:
    """One dependency Task's shape, grouped so `_seed_dependency` stays within codingrules 5.1."""

    role: WorkerRole
    status: TaskStatus
    scout_report: ScoutReport | None
    updated_at: datetime


async def _seed_dependency(
    store: MemoryTaskStore, clock: FakeClock, spec: _DependencySpec
) -> TaskId:
    """Insert one already-terminal dependency Task directly, bypassing chamber transitions.

    `_recon_for`'s one call is `BroodChamber.get`, a plain read-through to the store
    (`hivemind.brood_chamber.chamber.queries`), so seeding the store directly -- with whatever
    status, role and outcome a test wants -- is enough; no lifecycle transition needs to be legal.
    """
    task_id = new_task_id(clock)
    outcome = (
        make_outcome(
            status=spec.status, clock=clock, scout_report=spec.scout_report, verified_by=None
        )
        if spec.status is not TaskStatus.SUCCEEDED
        else make_outcome(status=spec.status, clock=clock, scout_report=spec.scout_report)
    )
    task = make_task(
        status=spec.status,
        clock=clock,
        id=task_id,
        goal_id=task_id,
        spec=make_task_spec(role=spec.role, clock=clock),
        outcome=outcome,
        updated_at=spec.updated_at,
    )
    event = TaskEvent(
        id=new_event_id(clock),  # Any well-formed id; this test never reads it back.
        hive_id=new_hive_id(clock),
        node_id=new_node_id(clock),
        at=spec.updated_at,
        actor="system",
        kind="task.submitted",
        subject_id=task_id,
        payload={},
    )
    await store.insert_tasks([task], [event])
    return task_id


def _make_chamber_and_store(clock: FakeClock) -> tuple[BroodChamber, MemoryTaskStore]:
    trail = MemoryPheromoneTrail(clock)
    store = MemoryTaskStore(trail)
    identity = ChamberIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return BroodChamber(store, clock, identity), store


async def test_recon_for_returns_empty_for_a_task_with_no_dependencies() -> None:
    deps, _link, warden_end = make_queen_deps()
    task = make_task(spec=make_task_spec(depends_on=()))

    recon = await _recon_for(deps, task)

    assert recon == ()
    await warden_end.close()


async def test_recon_for_skips_a_dependency_that_is_not_a_scout() -> None:
    deps, _link, warden_end = make_queen_deps()
    clock = FakeClock(_START)
    chamber, store = _make_chamber_and_store(clock)
    dep_id = await _seed_dependency(
        store,
        clock,
        _DependencySpec(WorkerRole.DRONE, TaskStatus.SUCCEEDED, None, _START),
    )
    deps = dataclasses.replace(deps, chamber=chamber)
    task = make_task(spec=make_task_spec(depends_on=(dep_id,)))

    recon = await _recon_for(deps, task)

    assert recon == ()
    await warden_end.close()


async def test_recon_for_skips_a_scout_dependency_that_did_not_succeed() -> None:
    deps, _link, warden_end = make_queen_deps()
    clock = FakeClock(_START)
    chamber, store = _make_chamber_and_store(clock)
    gave_up = ScoutReport(feasible=False, summary="Gave up.")
    dep_id = await _seed_dependency(
        store, clock, _DependencySpec(WorkerRole.SCOUT, TaskStatus.FAILED, gave_up, _START)
    )
    deps = dataclasses.replace(deps, chamber=chamber)
    task = make_task(spec=make_task_spec(depends_on=(dep_id,)))

    recon = await _recon_for(deps, task)

    assert recon == ()
    await warden_end.close()


async def test_recon_for_skips_a_succeeded_scout_with_no_report() -> None:
    deps, _link, warden_end = make_queen_deps()
    clock = FakeClock(_START)
    chamber, store = _make_chamber_and_store(clock)
    dep_id = await _seed_dependency(
        store, clock, _DependencySpec(WorkerRole.SCOUT, TaskStatus.SUCCEEDED, None, _START)
    )
    deps = dataclasses.replace(deps, chamber=chamber)
    task = make_task(spec=make_task_spec(depends_on=(dep_id,)))

    recon = await _recon_for(deps, task)

    assert recon == ()
    await warden_end.close()


async def test_recon_for_orders_newest_completion_first_and_caps_at_max_recon_reports() -> None:
    deps, _link, warden_end = make_queen_deps()
    clock = FakeClock(_START)
    chamber, store = _make_chamber_and_store(clock)
    reports = [ScoutReport(feasible=True, summary=f"Report {i}.") for i in range(5)]
    # Five SUCCEEDED Scouts, oldest (index 0) to newest (index 4); MAX_RECON_REPORTS is 4, so the
    # oldest must be the one left out.
    dep_ids = [
        await _seed_dependency(
            store,
            clock,
            _DependencySpec(
                WorkerRole.SCOUT, TaskStatus.SUCCEEDED, report, _START + timedelta(minutes=i)
            ),
        )
        for i, report in enumerate(reports)
    ]
    deps = dataclasses.replace(deps, chamber=chamber)
    task = make_task(spec=make_task_spec(depends_on=tuple(dep_ids)))

    recon = await _recon_for(deps, task)

    assert len(recon) == MAX_RECON_REPORTS
    # Newest (index 4) first, down to index 1; index 0 (the oldest) is dropped.
    assert recon == (reports[4], reports[3], reports[2], reports[1])
    await warden_end.close()


# ──────────────────────────────────────────────────────────────────────────────
# _task_assign: role and recon on the wire TaskAssign
# ──────────────────────────────────────────────────────────────────────────────


def test_task_assign_carries_the_task_s_own_role_and_the_given_recon() -> None:
    clock = FakeClock()
    report = ScoutReport(feasible=True, summary="The login form is at /login.")
    task = make_task(spec=make_task_spec(role=WorkerRole.SCOUT), clock=clock)
    terms = AssignmentTerms(attempt=1, recon=(report,))

    assign = _task_assign(task, new_cell_id(clock), new_grant_id(clock), terms)

    assert assign.role is WorkerRole.SCOUT
    assert assign.recon == (report,)


def test_task_assign_defaults_recon_to_empty() -> None:
    clock = FakeClock()
    task = make_task(spec=make_task_spec(role=WorkerRole.DRONE), clock=clock)
    terms = AssignmentTerms(attempt=1)

    assign = _task_assign(task, new_cell_id(clock), new_grant_id(clock), terms)

    assert assign.role is WorkerRole.DRONE
    assert assign.recon == ()
