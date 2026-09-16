"""End-to-end: kill the fake provider mid-run, cluster, wake, resume with no duplicated work.

Roadmap step 4.9's own exit criterion (`.claude/roadmap.md` phase 4): "Kill the fake provider
mid-run: the trail shows checkpoint -> PAUSED for every affected bee, leases stay open, `hive
wake` after restoring the provider resumes every task from its Handoff and the goal completes
with no duplicated work." `hive cluster`/`hive wake` are roadmap step 4.11 (a separate dispatch,
not built yet); this scenario writes the same durable `ClusterOrder` rows directly through
`hivemind.queen.cluster.orders.OrderStore` (docs/adr/0024's own "the same shape `hive inbox
answer` already uses") and drives `hivemind.queen.cluster.tick.run_cluster_tick` from this test's
own loop, exactly as `hive run`'s own poll loop would once step 4.11 wires it in -- this dispatch
may not edit `queen/queen.py` to wire that call in itself (see this dispatch's own report for the
exact lines the orchestrator should add there).

This test wires a real `Queen` to a real `Warden` over one real (in-process) Waggle link -- not
`tests.builders.queen`'s own `WardenEnd` stub, which only captures wire messages -- so the whole
checkpoint-then-pause-then-resume path runs through the real `hivemind.workers.runtime.
WorkerRuntime` a Warden actually spawns, the same one `hivemind.queen.cluster.protocol.cluster`'s
own `Intervene(HANDOFF)` reaches in production.

Fits into the Hive:
    Whole-Hive scenario (codingrules section 3, tests/e2e). Exercises `hivemind.queen.cluster`
    end to end.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 4 exit criteria for the scenario this test proves.
    - docs/adr/0024-clustering-protocol.md for the protocol this test drives.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.forage import make_source
from builders.memory import make_handoff
from builders.queen import make_queen_deps, plan_responder
from builders.wardens import make_warden_deps
from builders.workers import RunScript, ScriptedWorker, make_outcome

from hivemind.brood_chamber import Task, TaskFilter, TaskStatus
from hivemind.cell import Cell, CellKind, HoneyClearance
from hivemind.forage import ForageMap
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.cluster.orders import ClusterOrder, OrderKind, new_order_id
from hivemind.queen.cluster.tick import run_cluster_tick
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.queen import Queen
from hivemind.queen.state import ClusterState
from hivemind.wardens.deps import WardenDeps
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from waggle.clock import SystemClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import TaskId, WardenId, new_hive_id, new_node_id, new_warden_id
from waggle.messages.task import TaskAssign
from waggle.transport.memory import MemoryTransport

pytestmark = pytest.mark.e2e

_FIRST_OBJECTIVE = "Write the FIRST haiku."
_SECOND_OBJECTIVE = "Write the SECOND haiku."  # This task is the one clustering pauses.
_SETTLE_ROUNDS = (
    500  # Generous: a stalled scenario fails fast rather than hanging (no real sleeps).
)


def _plan() -> dict[str, object]:
    """Two independent tasks, so one can finish (task A) while the other is still in flight."""

    def _task(key: str, objective: str) -> dict[str, object]:
        return {
            "key": key,
            "title": objective,
            "objective": objective,
            "acceptance": [
                {
                    "kind": "FILE_EXISTS",
                    "subject": f"scratch/{key}.txt",
                    "argv": [],
                    "expected": None,
                }
            ],
            "needs": {},
            "clearance": "C1",
            "depends_on": [],
        }

    return {"tasks": [_task("first", _FIRST_OBJECTIVE), _task("second", _SECOND_OBJECTIVE)]}


def _make_script(
    second_fresh_starts: list[int],
) -> Callable[[WorkerContext, TaskAssign, object], Awaitable[WorkerOutcome]]:
    """Build the one RunScript both tasks share, told apart by `assignment.objective`.

    Args:
        second_fresh_starts: Appended to (with a plain `1`) every time the SECOND task's role
            starts *fresh* (never on a resumed attempt) -- the "no duplicated work" proof.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: object
    ) -> WorkerOutcome:
        # Every task's own acceptance is FILE_EXISTS on its own scratch file (module docstring's
        # `_plan`); the Warden's own acceptance check runs for real, so this really writes it.
        # The subject a task's own FILE_EXISTS postcondition names ("scratch/<key>.txt", `_plan`'s
        # own convention) is itself relative to `session.scratch_dir` (`CellSession.put_file`'s
        # own contract); write to that exact relative path, not an already-resolved absolute one.
        key = "first" if assignment.objective == _FIRST_OBJECTIVE else "second"
        target = Path(f"scratch/{key}.txt")
        if resume_from is not None:
            # A resumed attempt: the Handoff already carries everything; claim at once, no redo.
            await ctx.session.put_file(target, b"resumed")
            return make_outcome(summary="resumed: nothing left to redo")
        if assignment.objective == _SECOND_OBJECTIVE:
            second_fresh_starts.append(1)
            # ASYNC110: cooperative poll for the Queen-sent Intervene(HANDOFF), mirroring
            # hivemind.workers.roles.drone's own ctx.telemetry.handoff_requested check.
            while not ctx.telemetry.handoff_requested:  # noqa: ASYNC110
                await asyncio.sleep(0)
            return make_outcome(claimed=False, handoff=make_handoff())
        await ctx.session.put_file(target, b"done")
        return make_outcome(summary="first task done")

    return script


async def _wait_until(condition: Callable[[], Awaitable[bool]]) -> None:
    """Yield the event loop until `condition()` is true; a cheap, no-real-sleep settle loop."""
    for _ in range(_SETTLE_ROUNDS):
        if await condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true within the round budget")


async def _find_task(deps: QueenDeps, goal_id: TaskId, title: str) -> Task:
    """Return the one task under `goal_id` whose title is `title`."""
    tasks = await deps.chamber.list(TaskFilter(goal_id=goal_id))
    return next(task for task in tasks if task.spec.title == title)


async def _task_status_is(deps: QueenDeps, goal_id: TaskId, title: str, status: TaskStatus) -> bool:
    """True once the task titled `title` under `goal_id` reaches `status`."""
    task = await _find_task(deps, goal_id, title)
    return task.status is status


async def _task_reached(deps: QueenDeps, task_id: TaskId, status: TaskStatus) -> bool:
    """True once the task `task_id` reaches `status` (looked up by id, not by title)."""
    task = await deps.chamber.get(task_id)
    return task.status is status


async def _checkpoint_recorded(deps: QueenDeps) -> bool:
    """True once at least one `memory.checkpoint` trail event exists."""
    kinds = [event.kind for event in await deps.trail.query(TrailQuery())]
    return "memory.checkpoint" in kinds


@dataclasses.dataclass(frozen=True, slots=True)
class _Scenario:
    """Every collaborator this module's own e2e test drives, built by `_build_scenario`."""

    deps: QueenDeps
    queen: Queen
    warden: Warden
    provider: FakeLLMProvider
    second_fresh_starts: list[int]


@dataclasses.dataclass(frozen=True, slots=True)
class _Wiring:
    """The addresses and transport halves `_build_scenario` shares between Queen and Warden."""

    cell: Cell
    warden_id: WardenId
    queen_hop: Hop
    warden_hop: Hop
    queen_transport: MemoryTransport
    warden_transport: MemoryTransport


def _build_wiring(clock: SystemClock) -> _Wiring:
    """Build one REAL Cell and one Waggle link's own two hops and transport halves.

    `QueenEnd.pair_with`'s own docstring: "node_id: the sending node id both ends stamp (phase 3:
    one process, one node)" -- both hops below share the same `node_id` per that convention.
    """
    cell = make_cell(kind=CellKind.REAL, clock=clock)
    hive_id, node_id, warden_id = new_hive_id(clock), new_node_id(clock), new_warden_id(clock)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    return _Wiring(
        cell=cell,
        warden_id=warden_id,
        queen_hop=Hop(sender=hive_id, recipient=warden_id, node_id=node_id),
        warden_hop=Hop(sender=warden_id, recipient=hive_id, node_id=node_id),
        queen_transport=queen_transport,
        warden_transport=warden_transport,
    )


def _build_queen_deps(clock: SystemClock, provider: FakeLLMProvider, wiring: _Wiring) -> QueenDeps:
    """Build the QueenDeps over one REAL Cell with 8 seats.

    Roadmap-4.7's own headroom math otherwise floors a single-seat source's `max_sub_bees` to 0
    for a real Cell's own capacity.
    """
    forage_map = ForageMap(
        [make_source(source_id="fake-worker", provider="fake", model="test-model", seats=8)],
        clock=clock,
    )
    deps, _unused_link, _unused_end = make_queen_deps(
        clock=clock, fake_provider=provider, cell=wiring.cell, map=forage_map
    )
    return deps


def _build_warden_deps(
    clock: SystemClock,
    provider: FakeLLMProvider,
    wiring: _Wiring,
    deps: QueenDeps,
    script: RunScript,
) -> WardenDeps:
    """Build the WardenDeps, sharing the Queen's own `identity`/`memory`.

    See `_Wiring`'s own docstring: same node id, same store, so a checkpoint sorts before its own
    PAUSED and a later resume can find it.
    """
    warden_deps, _unused_queen_end, _ = make_warden_deps(
        clock=clock,
        cells=(wiring.cell,),
        warden_id=wiring.warden_id,
        worker_factory=lambda role: ScriptedWorker(script, role=role),
        fake_provider=provider,
        queen_link=wiring.warden_transport,
        hop=wiring.warden_hop,
        identity=deps.identity,
        memory=deps.memory,
    )
    return warden_deps


def _build_scenario(clock: SystemClock) -> _Scenario:
    """Wire one real Queen to one real Warden over one real (in-process) Waggle link."""
    provider = FakeLLMProvider(responder=plan_responder(lambda _goal: _plan()))
    wiring = _build_wiring(clock)
    second_fresh_starts: list[int] = []
    script = _make_script(second_fresh_starts)

    deps = _build_queen_deps(clock, provider, wiring)
    warden_deps = _build_warden_deps(clock, provider, wiring, deps, script)
    link = WardenLink(
        warden_id=wiring.warden_id,
        cell=wiring.cell,
        transport=wiring.queen_transport,
        hop=wiring.queen_hop,
    )
    queen = Queen(deps)
    queen.attach_warden(link)
    warden = Warden(wiring.warden_id, warden_deps)
    return _Scenario(
        deps=deps,
        queen=queen,
        warden=warden,
        provider=provider,
        second_fresh_starts=second_fresh_starts,
    )


async def _cluster_second_task(
    scenario: _Scenario, cluster_state: ClusterState, second_task: Task
) -> None:
    """Kill the fake provider, cluster it, and wait for the checkpoint -> PAUSED trail order."""
    deps, queen = scenario.deps, scenario.queen
    scenario.provider.set_outage(True)
    await deps.orders.put_order(
        ClusterOrder(
            id=new_order_id(deps.clock),
            kind=OrderKind.CLUSTER,
            provider="fake",
            requested_at=deps.clock.now(),
        )
    )
    await run_cluster_tick(deps, cluster_state, queen.wardens)

    await _wait_until(
        lambda: _fresh_started(scenario)
    )  # The role reached its own cooperative wait.
    await _wait_until(lambda: _task_reached(deps, second_task.id, TaskStatus.PAUSED))
    await _wait_until(lambda: _checkpoint_recorded(deps))

    # The trail shows checkpoint -> PAUSED for the affected bee; leases stay open (asserted by
    # the caller, which alone holds the lease captured before this call).
    trail_events = await deps.trail.query(TrailQuery())
    trail_kinds = [event.kind for event in trail_events]
    checkpoint_index = trail_kinds.index("memory.checkpoint")
    paused_index = trail_kinds.index("task.paused")
    assert checkpoint_index < paused_index, [(e.kind, e.subject_id) for e in trail_events]


async def _fresh_started(scenario: _Scenario) -> bool:
    """True once the SECOND task's own role has started exactly once, never restarted."""
    return len(scenario.second_fresh_starts) == 1


async def _wake_and_verify(
    scenario: _Scenario, cluster_state: ClusterState, goal_id: TaskId
) -> None:
    """`hive wake` after restoring the provider: the goal completes with no duplicated work."""
    deps, queen = scenario.deps, scenario.queen
    scenario.provider.set_outage(False)
    await deps.orders.put_order(
        ClusterOrder(
            id=new_order_id(deps.clock),
            kind=OrderKind.WAKE,
            provider="fake",
            requested_at=deps.clock.now(),
        )
    )
    await run_cluster_tick(deps, cluster_state, queen.wardens)

    await _wait_until(
        lambda: _task_status_is(deps, goal_id, _SECOND_OBJECTIVE, TaskStatus.SUCCEEDED)
    )
    assert scenario.second_fresh_starts == [1]  # No duplicated work: only ever started fresh once.


async def test_clustering_pauses_and_wake_resumes_with_no_duplicated_work() -> None:
    # A real clock, not FakeClock (kernel_helpers.py's own precedent: "most scenarios run their
    # Hive on a real SystemClock"): every trail event here is minted with the *same* FakeClock
    # reading otherwise (it never advances), so two events recorded moments apart in real,
    # causal order would tie on `at` and sort by ULID randomness instead -- exactly the kind of
    # flake this scenario's own "checkpoint before PAUSED" trail-order assertion cannot tolerate.
    clock = SystemClock()
    scenario = _build_scenario(clock)
    deps, queen, warden = scenario.deps, scenario.queen, scenario.warden
    await warden.start()
    queen_task = asyncio.ensure_future(queen.run())
    warden_task = asyncio.ensure_future(warden.run())

    goal_id = await queen.submit_goal("Write two haiku.", clearance=HoneyClearance.C1)

    # Wait for the FIRST Drone's own result (roadmap step 4.9's own exit criterion: "flip the fake
    # into outage after the first Drone result"), while the SECOND is still cooperatively waiting.
    await _wait_until(
        lambda: _task_status_is(deps, goal_id, _FIRST_OBJECTIVE, TaskStatus.SUCCEEDED)
    )

    second_task = await _find_task(deps, goal_id, _SECOND_OBJECTIVE)
    assert second_task.status is TaskStatus.RUNNING  # Sanity: still in flight when we cluster.
    lease_before = warden.lease
    assert lease_before is not None

    cluster_state = ClusterState()
    await _cluster_second_task(scenario, cluster_state, second_task)
    assert warden.lease is lease_before  # Same lease object: never released or re-leased.

    # `hive wake` after restoring the provider: write the durable WAKE row directly (roadmap step
    # 4.11 builds the CLI itself), then let the running Queen's own tick poll it.
    await _wake_and_verify(scenario, cluster_state, goal_id)

    # `queen.stop()`/`warden.stop()`, then await each `run()` task -- not a bare `.cancel()`,
    # which can leave a tick mid-flight and its background task un-reaped for a later test to
    # trip over (the pattern every other Queen/Warden e2e and unit test in this repo follows,
    # e.g. `tests.unit.queen.test_queen_alarms`).
    await queen.stop()
    await asyncio.wait_for(queen_task, timeout=5.0)
    await warden.stop()
    await asyncio.wait_for(warden_task, timeout=5.0)
