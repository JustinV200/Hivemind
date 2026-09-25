"""Tests for hivemind.queen.dispatcher.backoff: rest a backend whose provisions keep failing.

The defect these pin down: a Virtual backend that failed every provision was chosen again on
every dispatch pass that found a task waiting for it, so each pass started one more round and
each round wrote the lifecycle's `cell.provisioning` and `cell.provision_failed`: thousands of
rows an hour at the default tick. The first tests drive the backoff's own moves over
`builders.queen`'s fakes; the last two drive the real dispatch path, one pass a (fake) second,
over a Virtual provider that fails for as long as the test says, never sleeping.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/backoff.py (codingrules section 3), with its two
    callers on the dispatch path: acquire (each attempt's outcome) and snapshot (the hold).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.backoff for the module under test.
    - hivemind.queen.deps for ProvisionBackoff, the book it keeps.
"""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime

from builders.cells import make_cell
from builders.forage import make_capacity
from builders.queen import make_queen_deps, make_warden_link
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskStatus
from hivemind.cell import CellKind
from hivemind.hive import BackendCapabilities, CellProvisionError, VirtualCellSpec
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.dispatcher.backoff import HELD_BACK, mark_held, note_failed, note_served
from hivemind.queen.placement import (
    Placement,
    PlacementPolicy,
    ProvisionVirtual,
    ReuseDormant,
    VirtualBackendCandidate,
)
from waggle.clock import FakeClock
from waggle.ids import TaskId, new_cell_id, new_warden_id

_BACKEND = "fake"
_FAILURE = CellProvisionError(_BACKEND, "base-ubuntu", "daemon not running")
# A day of failures: more than any scenario here runs, so "always failing" in practice.
_ALWAYS = 86_400


@dataclass
class _Provider:
    """A VirtualCellProvider whose backend fails its first `failures` provisions, then serves."""

    clock: FakeClock
    cells: list[WardenLink] = field(default_factory=list)
    failures: int = _ALWAYS
    calls: list[datetime] = field(default_factory=list)

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Note when it was asked; fail while failures remain, else hand over the next Cell."""
        self.calls.append(self.clock.now())
        if len(self.calls) <= self.failures:
            raise _FAILURE
        return self.cells.pop(0)


def _spec(deps: QueenDeps) -> VirtualCellSpec:
    """The one spec the backend offers."""
    return VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        capacity=make_capacity(),
        hive_id=deps.identity.hive_id,
    )


def _candidate(deps: QueenDeps, name: str = _BACKEND) -> VirtualBackendCandidate:
    """One Virtual backend with no declared headroom limit."""
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True)
    return VirtualBackendCandidate(name=name, capabilities=capabilities, specs=(_spec(deps),))


def _rig(provider: _Provider) -> QueenDeps:
    """Deps whose one place for a task is the backend: no Warden is attached to the passes."""
    base, _stand, _stand_end = make_queen_deps(provider.clock)
    return dataclasses.replace(
        base,
        virtual_provider=provider,
        virtual_backends=(_candidate(base),),
        placement_policy=PlacementPolicy(prefer="virtual"),
    )


def _fresh_cell(deps: QueenDeps) -> WardenLink:
    """A freshly provisioned Virtual Cell's link, for a provision that succeeds."""
    cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock)
    identity = deps.identity
    warden_id = new_warden_id(deps.clock)
    link, _end = make_warden_link(identity.hive_id, warden_id, identity.node_id, cell, deps.clock)
    return link


async def _passes(deps: QueenDeps, seconds: int) -> None:
    """Run a dispatch pass a second for `seconds`, its acquisitions ended before the next."""
    for _ in range(seconds):
        # No attached Warden: a Cell the backend makes is the only place a task can go.
        await dispatch_ready(deps, ())
        jobs = [held.job for held in deps.dispatch.provisions.jobs.values()]
        if jobs:
            await asyncio.wait(jobs)  # A fake provider settles at once: no time passes meanwhile.
        assert isinstance(deps.clock, FakeClock)
        deps.clock.advance(1)


async def _decided(deps: QueenDeps, reason: str) -> list[PheromoneEvent]:
    """Every `queen.decided` row with `reason`, oldest first."""
    decided = await deps.trail.query(TrailQuery(kind="queen.decided"))
    return [event for event in decided if event.payload.get("reason") == reason]


async def _unplaced(deps: QueenDeps, task_id: TaskId) -> list[PheromoneEvent]:
    """Every `queen.decided` saying `task_id` found no Cell."""
    return [row for row in await _decided(deps, "placement_failed") if row.subject_id == task_id]


def _offsets(clock_start: datetime, times: list[datetime]) -> list[float]:
    """Each time, in seconds after `clock_start`."""
    return [(at - clock_start).total_seconds() for at in times]


async def test_each_failed_round_holds_its_backend_twice_as_long_up_to_the_cap() -> None:
    clock = FakeClock()
    deps = _rig(_Provider(clock))
    placement = ProvisionVirtual(_spec(deps), _BACKEND, "test")

    holds = []
    for _ in range(12):
        await note_failed(deps, placement, _FAILURE, clock.now())
        hold = deps.dispatch.backoff.holds[_BACKEND]
        holds.append(hold.hold_s)
        clock.advance(hold.hold_s)  # The next round starts only once this hold is over.

    assert holds == [1, 2, 4, 8, 16, 32, 64, 128, 256, 300, 300, 300]
    assert deps.dispatch.backoff.holds[_BACKEND].failures == 12
    # One row says the run began, with the first hold and the cause; the rounds after say nothing.
    [held] = await _decided(deps, "backend_held_back")
    assert held.subject_id == deps.identity.hive_id
    assert held.payload["backend"] == _BACKEND
    assert held.payload["hold_s"] == 1
    assert held.payload["detail"] == "daemon not running"


async def test_an_attempt_in_flight_when_its_round_failed_is_part_of_that_round() -> None:
    clock = FakeClock()
    deps = _rig(_Provider(clock))
    placement = ProvisionVirtual(_spec(deps), _BACKEND, "test")
    started = clock.now()
    clock.advance(5)  # Two provisions started together, and both took a while to fail.

    await note_failed(deps, placement, _FAILURE, started)
    await note_failed(deps, placement, _FAILURE, started)

    assert deps.dispatch.backoff.holds[_BACKEND].failures == 1
    assert deps.dispatch.backoff.holds[_BACKEND].hold_s == 1


async def test_a_held_backend_rests_then_is_tried_one_provision_at_a_time() -> None:
    clock = FakeClock()
    deps = _rig(_Provider(clock))
    backend, other = _candidate(deps), _candidate(deps, "other")
    await note_failed(deps, ProvisionVirtual(_spec(deps), _BACKEND, "test"), _FAILURE, clock.now())

    assert mark_held(deps, backend, 0).held_back == HELD_BACK
    assert mark_held(deps, other, 0) is other  # Only the backend that failed is held back.
    clock.advance(1)
    assert mark_held(deps, backend, 0) is backend  # Its hold is over: tried again.
    # One provision on it in flight is the run's next round; none starts beside it.
    assert mark_held(deps, backend, 1).held_back == HELD_BACK


async def test_a_provision_that_succeeds_ends_the_run_and_says_so_once() -> None:
    clock = FakeClock()
    deps = _rig(_Provider(clock))
    placement = ProvisionVirtual(_spec(deps), _BACKEND, "test")
    await note_failed(deps, placement, _FAILURE, clock.now())
    clock.advance(1)
    await note_failed(deps, placement, _FAILURE, clock.now())

    await note_served(deps, placement)
    await note_served(deps, placement)  # No run left to end: nothing more to say.

    assert deps.dispatch.backoff.holds == {}
    [restored] = await _decided(deps, "backend_restored")
    assert restored.payload["backend"] == _BACKEND
    assert restored.payload["failures"] == 2


async def test_a_dormant_cell_that_fails_to_resume_holds_no_backend_back() -> None:
    clock = FakeClock()
    deps = _rig(_Provider(clock))
    placement = ReuseDormant(new_cell_id(clock), new_warden_id(clock), "test")

    await note_failed(deps, placement, _FAILURE, clock.now())

    assert deps.dispatch.backoff.holds == {}
    assert await _decided(deps, "backend_held_back") == []


async def test_a_backend_that_fails_every_provision_is_tried_a_bounded_number_of_times() -> None:
    clock = FakeClock()
    provider = _Provider(clock)
    deps = _rig(provider)
    start = clock.now()
    tasks = await deps.chamber.submit(make_graph_draft({"a": (), "b": (), "c": ()}))

    await _passes(deps, 1_200)  # Twenty minutes of one pass a second.

    # Before, every task waiting started a round every other pass: some 1,800 provisions. Now
    # the first three fail together as one round, and each round after is one provision, once
    # the hold of the round before it is over: 1, 2, 4 ... 256 s, then the 300 s cap.
    rounds = [2, 4, 8, 16, 32, 64, 128, 256, 512, 812, 1112]
    assert _offsets(start, provider.calls) == [0, 0, 0, *rounds]
    # One row said the backend is held back; each task was said to wait once, and waits still.
    assert len(await _decided(deps, "backend_held_back")) == 1
    assert await _decided(deps, "backend_restored") == []
    for task in tasks:
        assert len(await _unplaced(deps, task.id)) == 1
        assert (await deps.chamber.get(task.id)).status is TaskStatus.PENDING


async def test_a_backend_that_recovers_is_used_again() -> None:
    clock = FakeClock()
    provider = _Provider(clock, failures=3)
    deps = _rig(provider)
    provider.cells.extend([_fresh_cell(deps), _fresh_cell(deps)])
    start = clock.now()
    [first] = await deps.chamber.submit(make_graph_draft({"first": ()}))

    await _passes(deps, 10)

    # Three rounds failed (held back 1, 2 and 4 s); the fourth made the Cell, and ended the run.
    assert _offsets(start, provider.calls) == [0, 2, 4, 8]
    assert (await deps.chamber.get(first.id)).status is TaskStatus.RUNNING
    assert deps.dispatch.backoff.holds == {}
    [restored] = await _decided(deps, "backend_restored")
    assert restored.payload["failures"] == 3
    # Used again at once: the next task's provision starts on the very next pass.
    [second] = await deps.chamber.submit(make_graph_draft({"second": ()}))
    await _passes(deps, 2)
    assert _offsets(start, provider.calls)[-1] == 10
    assert (await deps.chamber.get(second.id)).status is TaskStatus.RUNNING
