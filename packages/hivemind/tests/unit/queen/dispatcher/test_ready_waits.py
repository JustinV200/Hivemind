"""Tests for hivemind.queen.dispatcher.ready's zero-grant path: wait, retry, expire, or deny.

The defect these pin down: a grant zeroed by a passing reading (the Hive Stand's load right now)
used to fail its task at once, so any moment of host load failed every goal. Every scenario here
drives the real dispatch path (`Queen.submit_goal`, then `dispatch_ready` for each later pass)
over `builders.queen`'s fakes, with the Hive Stand's live reading injected through
`WardenLink.live_capacity` and time moved only by the FakeClock, never slept.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/ready.py (codingrules section 3), split by feature
    (14.2) from tests/unit/queen/test_queen_dispatch.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.ready, .sizing and .zero_grant for the code under test.
"""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator, Callable, Mapping

import pytest
from builders.cells import make_cell
from builders.forage import make_capacity, make_footprint, make_host_capacity
from builders.human import single_task_plan
from builders.queen import FORAGE_SOURCE_SEATS, WardenEnd, make_queen_deps, plan_responder
from builders.virtual_cells import independent_haiku_plan

from hivemind.brood_chamber import BroodChamber, TaskFilter, TaskOutcome, TaskStatus
from hivemind.cell import Cell, CellKind, HoneyClearance
from hivemind.forage import ForageCapacity, GoalBudgets, RoyalReserve
from hivemind.llm import FakeLLMProvider
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready, redispatch
from hivemind.queen.queen import Queen
from waggle.clock import FakeClock
from waggle.envelope import Envelope
from waggle.ids import TaskId
from waggle.messages.task import WorkerRole
from waggle.transport.base import Transport

_MIB = 1024**2
_PATIENCE_S = 300.0  # QueenDeps' own default: the manifest's own [forage] default.
# 8 cores (builders.forage's own host) at a load of 7.9: 0.1 free, under one 1-core Drone.
_BUSY = make_capacity(host=make_host_capacity(cpu_load=7.9 / 8))
_IDLE = make_capacity()  # The same host at a load of 1.6: six Drones' worth of free cores.
_GOAL = "Write a haiku."
_ONLY_THE_BINDING_ORDERS = ("CeilingsSet", "PlanWritten")  # A Warden's first dispatch, no grant.


class _Reading:
    """A Hive Stand's live capacity reading a test sets, read through `WardenLink.live_capacity`."""

    def __init__(self, capacity: ForageCapacity) -> None:
        """Start the reading at `capacity`."""
        self.capacity = capacity

    async def read(self) -> ForageCapacity:
        """Return the reading as it stands now."""
        return self.capacity


class _Recorder:
    """The Queen's end of the link, noting every task's chamber status at each send."""

    def __init__(self, inner: Transport, chamber: BroodChamber) -> None:
        """Wrap `inner`, reading statuses from `chamber`."""
        self._inner = inner
        self._chamber = chamber
        self.sent: list[tuple[str, dict[TaskId, TaskStatus]]] = []

    @property
    def is_connected(self) -> bool:
        """Delegate to the wrapped transport."""
        return self._inner.is_connected

    async def connect(self) -> None:
        """Delegate to the wrapped transport."""
        await self._inner.connect()

    async def send(self, envelope: Envelope) -> None:
        """Note the message's kind and every task's status now, then send it."""
        statuses = {task.id: task.status for task in await self._chamber.list(TaskFilter())}
        self.sent.append((type(envelope.payload).__name__, statuses))
        await self._inner.send(envelope)

    def receive(self) -> AsyncIterator[Envelope]:
        """Delegate to the wrapped transport."""
        return self._inner.receive()

    async def close(self) -> None:
        """Delegate to the wrapped transport."""
        await self._inner.close()

    def kinds(self) -> list[str]:
        """Return the kind of every message sent, in order."""
        return [kind for kind, _ in self.sent]


@dataclasses.dataclass(frozen=True)
class _Stand:
    """One Queen over one attached link, and the handles a test drives it through."""

    queen: Queen
    deps: QueenDeps
    link: WardenLink
    recorder: _Recorder
    end: WardenEnd  # Held so the Warden's end of the pair lives as long as the test.


@dataclasses.dataclass(frozen=True)
class _Setup:
    """How one test's Queen is built, beyond its live reading (codingrules 5.1's argument group).

    Attributes:
        plan: What the Queen's planner answers for a goal.
        clock: The FakeClock every collaborator shares; a fresh one when None.
        cell: The attached link's Cell; `builders.cells.make_cell`'s Real one when None.
        overrides: QueenDeps field values replacing `builders.queen.make_queen_deps`' defaults.
    """

    plan: Callable[[str], dict[str, object]] = single_task_plan
    clock: FakeClock | None = None
    cell: Cell | None = None
    overrides: Mapping[str, object] = dataclasses.field(default_factory=dict)


async def _stand(reading: _Reading | None, setup: _Setup | None = None) -> _Stand:
    """Build and attach a Queen whose link reads `reading` live (None: a fixed Cell)."""
    active = setup if setup is not None else _Setup()
    provider = FakeLLMProvider(responder=plan_responder(active.plan))
    deps, link, end = make_queen_deps(
        active.clock, fake_provider=provider, cell=active.cell, **active.overrides
    )
    recorder = _Recorder(link.transport, deps.chamber)
    live = reading.read if reading is not None else None
    link = dataclasses.replace(link, transport=recorder, live_capacity=live)
    queen = Queen(deps)
    await queen.attach_warden(link)
    return _Stand(queen=queen, deps=deps, link=link, recorder=recorder, end=end)


async def _denials(deps: QueenDeps) -> list[PheromoneEvent]:
    """Return every `forage.denied` on the trail, oldest first."""
    return [
        event for event in await deps.trail.query(TrailQuery()) if event.kind == "forage.denied"
    ]


async def _kinds(deps: QueenDeps) -> list[str]:
    """Return every trail event's kind, oldest first."""
    return [event.kind for event in await deps.trail.query(TrailQuery())]


async def test_a_busy_hive_stand_defers_the_task_with_one_event_and_sends_nothing() -> None:
    stand = await _stand(_Reading(_BUSY))

    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)
    for _ in range(3):
        await dispatch_ready(stand.deps, [stand.link])  # Later passes: tried again, quietly.

    assert (await stand.deps.chamber.get(goal_id)).status is TaskStatus.PENDING
    [wait] = await _denials(stand.deps)
    assert wait.subject_id == goal_id
    assert wait.payload["deferred"] is True
    assert wait.payload["limited_by"] == "free_cores"
    assert wait.payload["patience_s"] == _PATIENCE_S
    assert wait.payload["cores"] == 8
    assert wait.payload["cpu_load"] == pytest.approx(7.9 / 8)
    assert wait.payload["footprint_cpu_cores"] == 1.0
    # Nothing reached the Warden, and the chamber never moved: no placement was even recorded.
    assert stand.recorder.sent == []
    kinds = await _kinds(stand.deps)
    assert "queen.placed" not in kinds and "queen.assigned" not in kinds


async def test_a_deferred_task_runs_once_the_load_drops_and_sends_only_when_running() -> None:
    reading = _Reading(_BUSY)
    stand = await _stand(reading)
    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)

    reading.capacity = _IDLE
    await dispatch_ready(stand.deps, [stand.link])

    assert (await stand.deps.chamber.get(goal_id)).status is TaskStatus.RUNNING
    assert stand.recorder.kinds() == [*_ONLY_THE_BINDING_ORDERS, "GrantIssued", "TaskAssign"]
    # The ordering invariant: every message left while the chamber already read RUNNING.
    assert all(statuses[goal_id] is TaskStatus.RUNNING for _, statuses in stand.recorder.sent)
    assert stand.deps.dispatch.waits.waits == {}
    assert len(await _denials(stand.deps)) == 1  # The wait's own event, and no other.
    assert "forage.granted" in await _kinds(stand.deps)


async def test_a_wait_past_its_patience_fails_the_task_with_the_figures() -> None:
    clock = FakeClock()
    stand = await _stand(_Reading(_BUSY), _Setup(clock=clock))
    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)

    clock.advance(_PATIENCE_S - 1)
    await dispatch_ready(stand.deps, [stand.link])
    assert (await stand.deps.chamber.get(goal_id)).status is TaskStatus.PENDING
    clock.advance(2)
    await dispatch_ready(stand.deps, [stand.link])

    task = await stand.deps.chamber.get(goal_id)
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    assert "zero sub-bees after waiting" in task.outcome.summary
    assert "free_cores" in task.outcome.summary
    _wait, denial = await _denials(stand.deps)
    assert denial.payload["deferred"] is False
    assert denial.payload["limited_by"] == "free_cores"
    waited_s = denial.payload["waited_s"]
    assert isinstance(waited_s, float) and waited_s >= _PATIENCE_S
    assert denial.payload["cpu_load"] == pytest.approx(7.9 / 8)
    # The wait's own event first; the denial after the chamber moved, the failure last of all.
    kinds = await _kinds(stand.deps)
    assert kinds.index("forage.denied") < kinds.index("queen.assigned")
    assert kinds[-2:] == ["forage.denied", "task.failed"]
    # Only the Warden's first orders ever left, and only once the chamber read RUNNING.
    assert stand.recorder.kinds() == list(_ONLY_THE_BINDING_ORDERS)
    assert all(statuses[goal_id] is TaskStatus.RUNNING for _, statuses in stand.recorder.sent)
    assert stand.deps.dispatch.waits.waits == {}


_TWO_CORE_ROLE = {WorkerRole.DRONE: make_footprint(cpu_cores=2.0)}
# A Cell whose own cap is zero is no longer among them: placement reads the Cell's live figures
# now (hivemind.queen.dispatcher.snapshot), so no task is ever placed on it to be denied there.
_LASTING = [
    pytest.param(
        _Reading(
            make_capacity(
                host=make_host_capacity(memory_bytes=768 * _MIB, memory_free_bytes=768 * _MIB)
            )
        ),
        {},
        None,
        "free_memory",
        id="total-memory",
    ),
    pytest.param(
        _Reading(make_capacity(host=make_host_capacity(cores=1, cpu_load=0.0))),
        {"footprints": _TWO_CORE_ROLE},
        None,
        "free_cores",
        id="total-cores",
    ),
    pytest.param(
        _Reading(_IDLE),
        {"reserve": RoyalReserve(seats=FORAGE_SOURCE_SEATS, memory_bytes=0)},
        None,
        "seats",
        id="no-seat",
    ),
    pytest.param(
        _Reading(_IDLE),
        {},
        ("cell:real:*", "cell:comb_shield:*", "tool:*"),
        None,
        id="guard-removed-every-binding",
    ),
]


@pytest.mark.parametrize(("reading", "overrides", "capabilities", "limited_by"), _LASTING)
async def test_a_lasting_shortfall_fails_the_task_at_once_with_the_same_denial(
    reading: _Reading,
    overrides: dict[str, object],
    capabilities: tuple[str, ...] | None,
    limited_by: str | None,
) -> None:
    stand = await _stand(reading, _Setup(overrides=overrides))

    goal_id = await stand.queen.submit_goal(
        _GOAL, clearance=HoneyClearance.C1, capabilities=capabilities
    )

    task = await stand.deps.chamber.get(goal_id)
    assert task.status is TaskStatus.FAILED
    assert task.outcome is not None
    assert task.outcome.summary.startswith("Forage denied: grant allows ")
    assert "after waiting" not in task.outcome.summary
    [denial] = await _denials(stand.deps)
    assert denial.payload["deferred"] is False
    assert denial.payload["limited_by"] == limited_by
    assert denial.payload["waited_s"] is None
    assert stand.recorder.kinds() == list(_ONLY_THE_BINDING_ORDERS)
    assert stand.deps.dispatch.waits.waits == {}


async def test_a_virtual_cells_zero_grant_fails_at_once_its_capacity_being_fixed() -> None:
    # A Virtual Cell's link carries no live reader: its resources are its spec, for its life.
    busy_virtual = make_cell(kind=CellKind.VIRTUAL, capacity=_BUSY)
    stand = await _stand(None, _Setup(cell=busy_virtual))

    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)

    assert (await stand.deps.chamber.get(goal_id)).status is TaskStatus.FAILED
    [denial] = await _denials(stand.deps)
    assert denial.payload["deferred"] is False
    assert denial.payload["limited_by"] == "free_cores"


def _two_haiku(_goal: str) -> dict[str, object]:
    """Plan two independent tasks, so the goal's second can start while its first runs."""
    return independent_haiku_plan(("haiku_1.txt", "haiku_2.txt"))


async def test_a_goal_whose_running_task_holds_its_allowance_defers_the_next_task() -> None:
    budgets = GoalBudgets(spend_cap_usd=5.0, token_budget=2_000_000, max_sub_bees=2)
    stand = await _stand(_Reading(_IDLE), _Setup(plan=_two_haiku, overrides={"budgets": budgets}))

    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)

    tasks = await stand.deps.chamber.list(TaskFilter(goal_id=goal_id))
    [running] = [task for task in tasks if task.status is TaskStatus.RUNNING]
    [waiting] = [task for task in tasks if task.status is TaskStatus.PENDING]
    [wait] = await _denials(stand.deps)
    assert wait.subject_id == waiting.id
    assert wait.payload["limited_by"] == "goal_bees"
    assert wait.payload["patience_s"] is None  # Waits on its own goal, not on the host.
    assert wait.payload["goal_sub_bees_used"] == 1
    assert wait.payload["goal_cap"] == 2
    # Held before any Cell was chosen: only the running task was ever placed.
    placed = [e for e in await stand.deps.trail.query(TrailQuery()) if e.kind == "queen.placed"]
    assert [event.subject_id for event in placed] == [running.id]

    outcome = TaskOutcome(
        status=TaskStatus.SUCCEEDED, summary="Done.", verified_by=stand.link.warden_id
    )
    await stand.deps.chamber.complete(running.id, outcome)
    await dispatch_ready(stand.deps, [stand.link])

    assert (await stand.deps.chamber.get(waiting.id)).status is TaskStatus.RUNNING
    assert len(await _denials(stand.deps)) == 1


def _one_or_two(goal: str) -> dict[str, object]:
    """Plan two independent tasks for the first goal, one task for any other."""
    return _two_haiku(goal) if goal.startswith("Two") else single_task_plan(goal)


async def test_a_waiting_task_never_holds_up_a_later_goals_task_in_the_same_pass() -> None:
    budgets = GoalBudgets(spend_cap_usd=5.0, token_budget=2_000_000, max_sub_bees=2)
    stand = await _stand(_Reading(_IDLE), _Setup(plan=_one_or_two, overrides={"budgets": budgets}))
    await stand.queen.submit_goal("Two haiku.", clearance=HoneyClearance.C1)

    later_goal = await stand.queen.submit_goal("One haiku.", clearance=HoneyClearance.C1)

    # The earlier goal's second task is still waiting, earlier in the ready order than this one.
    assert (await stand.deps.chamber.get(later_goal)).status is TaskStatus.RUNNING
    assert len(stand.deps.dispatch.waits.waits) == 1


async def test_a_waiting_task_that_is_cancelled_waits_no_longer() -> None:
    stand = await _stand(_Reading(_BUSY))
    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)
    assert set(stand.deps.dispatch.waits.waits) == {goal_id}

    await stand.deps.chamber.cancel(goal_id, "The human cancelled the goal.")
    await dispatch_ready(stand.deps, [stand.link])

    assert stand.deps.dispatch.waits.waits == {}


async def test_a_retry_is_sized_from_the_cell_as_probed_so_a_busy_moment_never_fails_it() -> None:
    # A RUNNING task cannot wait PENDING, so its retry never reads the live figures at all.
    reading = _Reading(_IDLE)
    stand = await _stand(reading)
    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)
    assert (await stand.deps.chamber.get(goal_id)).status is TaskStatus.RUNNING

    reading.capacity = _BUSY
    await redispatch(stand.deps, [stand.link], goal_id, attempt=1)

    assert (await stand.deps.chamber.get(goal_id)).status is TaskStatus.RUNNING
    assert await _denials(stand.deps) == []
    assert stand.recorder.kinds()[-2:] == ["GrantIssued", "TaskAssign"]


async def test_a_wait_whose_tightest_host_figure_changes_is_still_one_wait_on_one_clock() -> None:
    clock = FakeClock()
    reading = _Reading(_BUSY)
    stand = await _stand(reading, _Setup(clock=clock))
    goal_id = await stand.queen.submit_goal(_GOAL, clearance=HoneyClearance.C1)
    started = stand.deps.dispatch.waits.waits[goal_id].since

    # The cores free up but the memory fills: the host still has no room for a bee.
    full = make_host_capacity(memory_free_bytes=600 * _MIB)
    reading.capacity = make_capacity(host=full)
    clock.advance(10.0)
    await dispatch_ready(stand.deps, [stand.link])

    assert (await stand.deps.chamber.get(goal_id)).status is TaskStatus.PENDING
    assert len(await _denials(stand.deps)) == 1
    assert stand.deps.dispatch.waits.waits[goal_id].since == started
