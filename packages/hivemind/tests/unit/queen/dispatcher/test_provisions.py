"""Tests for hivemind.queen.dispatcher.provisions: Virtual Cells acquired beside the Queen's tick.

The defects these pin down: a Virtual Cell's provision ran inside the dispatch pass, so a slow one
stalled the Queen's whole tick (her inbox, her Wardens' liveness, the Entrance's goals); a Cell
made for a task that was cancelled, or whose grant was then denied, was left behind for good; and
a Cell was made for a task whose grant there could run no bee (its seat wait spent, or its spec
too small), only to be denied and released.
Every scenario drives the real dispatch path over `builders.queen`'s fakes and a Virtual provider
whose acquisition lands only when the test says so, waiting on state for a bounded number of loop
turns, never on a timer.

Fits into the Hive:
    Mirrors src/hivemind/queen/dispatcher/provisions.py (codingrules section 3), with the Queen's
    own tick and stop, its two callers besides the dispatch pass.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.dispatcher.provisions and .ready for the code under test.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from builders.cells import make_cell
from builders.forage import make_capacity
from builders.queen import WardenEnd, land_provisions, make_queen_deps
from builders.supervision import make_telemetry
from builders.tasks import make_graph_draft

from hivemind.brood_chamber import Task, TaskOutcome, TaskStatus
from hivemind.cell import CellKind
from hivemind.forage import ForageCapacity
from hivemind.hive import BackendCapabilities, VirtualCellSpec
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.dispatcher import dispatch_ready
from hivemind.queen.dispatcher.provisions import provisioning
from hivemind.queen.errors import UnknownWardenError
from hivemind.queen.placement import Placement, PlacementPolicy, VirtualBackendCandidate
from hivemind.queen.queen import Queen
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Hop
from waggle.ids import CellId, WardenId, new_warden_id
from waggle.messages.supervision import Heartbeat, WardenState
from waggle.transport.memory import MemoryTransport

_POLL_LIMIT = 4_000  # Loop turns a state wait may take before the test fails instead of hanging.
_SOURCE_ID = "fake-worker"  # builders.queen's one Forage map source.


@dataclass
class _GatedProvider:
    """A VirtualCellProvider whose acquisitions land only once the test sets `land`."""

    cells: list[WardenLink]
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    land: asyncio.Event = field(default_factory=asyncio.Event)
    calls: list[Task] = field(default_factory=list)

    async def acquire(self, placement: Placement, task: Task) -> WardenLink:
        """Note the task, then hand back the next Cell once the test lets it land."""
        self.calls.append(task)
        self.entered.set()
        await self.land.wait()
        return self.cells[len(self.calls) - 1]


@dataclass
class _Releases:
    """The release chain's stand-in (`QueenDeps.on_task_finished`): every Cell it was handed."""

    cells: list[tuple[CellId, TaskStatus]] = field(default_factory=list)

    async def __call__(self, cell_id: CellId, outcome: TaskOutcome) -> None:
        """Note the Cell and the outcome it was released with."""
        self.cells.append((cell_id, outcome.status))


@dataclass(frozen=True)
class _Rig:
    """A Queen's deps over one attached Warden, a gated Virtual provider and its fresh Cells."""

    deps: QueenDeps
    stand: WardenLink
    stand_end: WardenEnd
    provider: _GatedProvider
    cell_ends: tuple[WardenEnd, ...]
    releases: _Releases


def _rig(
    clock: FakeClock | None = None,
    *,
    cells: int = 1,
    limit: int = 4,
    fixed: ForageCapacity | None = None,
    promised: ForageCapacity | None = None,
) -> _Rig:
    """Build deps that place every task on a fresh Virtual Cell, at most `limit` at once.

    `fixed` is the capacity each fresh Cell reports once made; `promised`, the one its spec
    promises before (both `builders.forage.make_capacity()` when None).
    """
    base, stand, stand_end = make_queen_deps(clock)
    pairs = [_fresh_cell(base, fixed) for _ in range(cells)]
    provider = _GatedProvider(cells=[link for link, _end in pairs])
    releases = _Releases()
    deps = dataclasses.replace(
        base,
        virtual_provider=provider,
        virtual_backends=(_backend(base, promised or make_capacity()),),
        placement_policy=PlacementPolicy(prefer="virtual"),
        on_task_finished=releases,
    )
    deps.dispatch.provisions.limit = limit
    ends = tuple(end for _link, end in pairs)
    return _Rig(deps, stand, stand_end, provider, ends, releases)


def _fresh_cell(deps: QueenDeps, fixed: ForageCapacity | None) -> tuple[WardenLink, WardenEnd]:
    """One freshly provisioned Virtual Cell's link, and its Warden's end of the wire."""
    capacity = fixed if fixed is not None else make_capacity()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=deps.clock, capacity=capacity)
    warden_id = new_warden_id(deps.clock)
    queen_end, warden_end = MemoryTransport.pair(Codec(), Codec())
    node_id = deps.identity.node_id
    link = WardenLink(
        warden_id=warden_id,
        cell=cell,
        transport=queen_end,
        hop=Hop(sender=deps.identity.hive_id, recipient=warden_id, node_id=node_id),
    )
    hop = Hop(sender=warden_id, recipient=deps.identity.hive_id, node_id=node_id)
    return link, WardenEnd(warden_end, hop, deps.clock)


def _backend(deps: QueenDeps, promised: ForageCapacity) -> VirtualBackendCandidate:
    """One Virtual backend with room to spare, its spec promising `promised`."""
    spec = VirtualCellSpec(
        image="base-ubuntu",
        cpu_cores=1.0,
        memory_bytes=1024**3,
        disk_bytes=8 * 1024**3,
        capacity=promised,
        hive_id=deps.identity.hive_id,
    )
    capabilities = BackendCapabilities(can_snapshot=False, can_pause=True)
    return VirtualBackendCandidate(name="fake", capabilities=capabilities, specs=(spec,))


async def _wait_until(condition: Callable[[], bool | Awaitable[bool]]) -> None:
    """Yield the event loop until `condition()` (plain or awaitable) holds, or fail."""
    for _ in range(_POLL_LIMIT):
        result = condition()
        if isinstance(result, Awaitable):
            result = await result
        if result:
            return
        await asyncio.sleep(0)
    raise AssertionError("Condition never became true.")


async def _status(deps: QueenDeps, task: Task) -> TaskStatus:
    """The task's status as the Brood Chamber holds it now."""
    return (await deps.chamber.get(task.id)).status


async def _denials(deps: QueenDeps) -> list[PheromoneEvent]:
    """Every `forage.denied` on the trail, oldest first."""
    return list(await deps.trail.query(TrailQuery(kind="forage.denied")))


async def _released(deps: QueenDeps) -> list[PheromoneEvent]:
    """Every `queen.decided` row that released a Cell, oldest first."""
    decided = await deps.trail.query(TrailQuery(kind="queen.decided"))
    return [event for event in decided if event.payload.get("reason") == "cell_released"]


async def test_a_pass_starts_a_provision_beside_itself_and_never_awaits_it() -> None:
    rig = _rig()
    [task] = await rig.deps.chamber.submit(make_graph_draft({"root": ()}))

    dispatching = asyncio.ensure_future(dispatch_ready(rig.deps, (rig.stand,)))
    await _wait_until(rig.provider.entered.is_set)
    # The acquisition is still in flight, yet the pass is over: it held the tick no longer.
    await _wait_until(dispatching.done)

    assert await _status(rig.deps, task) is TaskStatus.PENDING
    assert provisioning(rig.deps, task.id)
    rig.provider.land.set()
    await land_provisions(rig.deps, (rig.stand,))
    assert await _status(rig.deps, task) is TaskStatus.RUNNING
    assert (await rig.cell_ends[0].wait_for_assignment()).task_id == task.id


async def test_no_more_cells_are_provisioned_at_once_than_the_lane_allows() -> None:
    rig = _rig(cells=2, limit=1)
    tasks = await rig.deps.chamber.submit(make_graph_draft({"a": (), "b": ()}))

    await dispatch_ready(rig.deps, (rig.stand,))
    in_flight = list(rig.deps.dispatch.provisions.jobs)
    rig.provider.land.set()
    await land_provisions(rig.deps, (rig.stand,))  # Collects the first, and starts the second.
    await land_provisions(rig.deps, (rig.stand,))

    # Exactly one started (whichever is earlier in the ready order); the other waited its turn.
    [first] = in_flight
    later = next(task.id for task in tasks if task.id != first)
    assert [task.id for task in rig.provider.calls] == [first, later]
    for task in tasks:
        assert await _status(rig.deps, task) is TaskStatus.RUNNING


async def test_a_cell_made_for_a_task_cancelled_meanwhile_is_released() -> None:
    rig = _rig()
    [task] = await rig.deps.chamber.submit(make_graph_draft({"root": ()}))
    await dispatch_ready(rig.deps, (rig.stand,))

    await rig.deps.chamber.cancel(task.id, "The human withdrew the goal.")
    rig.provider.land.set()
    await land_provisions(rig.deps, (rig.stand,))

    cell_id = rig.provider.cells[0].cell.id
    assert rig.releases.cells == [(cell_id, TaskStatus.CANCELLED)]
    [released] = await _released(rig.deps)
    assert released.payload["cause"] == "task_gone"
    assert released.payload["cell_id"] == cell_id
    assert rig.deps.dispatch.provisions.jobs == {}
    assert await rig.deps.trail.query(TrailQuery(kind="queen.assigned")) == ()


async def test_a_cell_made_for_a_task_whose_grant_is_denied_is_released() -> None:
    # The fresh Cell's own cap allows no bee, fixed for its life: its grant is denied at once.
    rig = _rig(fixed=make_capacity(max_sub_bees=0))
    [task] = await rig.deps.chamber.submit(make_graph_draft({"root": ()}))
    rig.provider.land.set()

    await dispatch_ready(rig.deps, (rig.stand,))
    await land_provisions(rig.deps, (rig.stand,))

    assert await _status(rig.deps, task) is TaskStatus.FAILED
    cell_id = rig.provider.cells[0].cell.id
    assert rig.releases.cells == [(cell_id, TaskStatus.FAILED)]
    [released] = await _released(rig.deps)
    assert released.payload["cause"] == "grant_denied"
    assert rig.deps.dispatch.provisions.jobs == {}


async def test_no_cell_is_made_for_a_task_whose_seat_wait_ran_out() -> None:
    # Every seat its tempo may use stays busy past [forage] zero_grant_patience_s.
    clock = FakeClock()
    rig = _rig(clock)
    await rig.deps.map.set_abundance(_SOURCE_ID, seats_free=0)
    [task] = await rig.deps.chamber.submit(make_graph_draft({"root": ()}))
    await dispatch_ready(rig.deps, (rig.stand,))  # The wait begins.
    clock.advance(rig.deps.dispatch.waits.patience_s + 1)

    await dispatch_ready(rig.deps, (rig.stand,))

    # Nothing was started for it: no acquisition in the lane, no call to the provider.
    assert rig.deps.dispatch.provisions.jobs == {}
    assert rig.provider.calls == []
    refused = await rig.deps.chamber.get(task.id)
    assert refused.status is TaskStatus.CANCELLED
    assert refused.outcome is not None and "after waiting" in refused.outcome.summary
    _wait, denial = await _denials(rig.deps)
    assert denial.subject_id == task.id
    assert denial.payload["deferred"] is False
    assert denial.payload["limited_by"] == "seats"
    assert denial.payload["cell_id"] is None
    waited_s = denial.payload["waited_s"]
    assert isinstance(waited_s, float) and waited_s >= rig.deps.dispatch.waits.patience_s


async def test_no_cell_is_made_whose_own_figures_could_run_no_bee() -> None:
    # The spec promises one sub-bee: the headroom margin leaves no whole bee of it, for good.
    rig = _rig(promised=make_capacity(max_sub_bees=1))
    [task] = await rig.deps.chamber.submit(make_graph_draft({"root": ()}))

    await dispatch_ready(rig.deps, (rig.stand,))

    assert rig.deps.dispatch.provisions.jobs == {}  # Nothing was started for it.
    assert rig.provider.calls == []
    assert await _status(rig.deps, task) is TaskStatus.CANCELLED
    [denial] = await _denials(rig.deps)
    assert denial.payload["limited_by"] == "cell_cap"
    assert denial.payload["deferred"] is False
    assert denial.payload["waited_s"] is None


async def test_her_tick_keeps_hearing_her_warden_while_a_cell_is_provisioned() -> None:
    clock = FakeClock()
    rig = _rig(clock)
    queen = Queen(rig.deps)
    await queen.attach_warden(rig.stand)
    [task] = await rig.deps.chamber.submit(make_graph_draft({"root": ()}))
    run_task = asyncio.ensure_future(queen.run())
    await _wait_until(rig.provider.entered.is_set)

    # Every Heartbeat is handled by her tick while the provision is still held: none waits on it.
    for beat in range(1, 4):
        clock.advance(rig.deps.heartbeat_interval_s)
        await rig.stand_end.send(_heartbeat(beat))
        await _wait_until(_handled_by(queen, rig.stand.warden_id, beat))
    still_provisioning = provisioning(rig.deps, task.id)
    rig.provider.land.set()
    await _wait_until(lambda: _is_running(rig.deps, task))
    await queen.stop()
    await asyncio.wait_for(run_task, timeout=5.0)

    assert still_provisioning


async def test_stopping_the_queen_awaits_a_provision_in_flight_and_starts_no_more() -> None:
    rig = _rig(cells=2)
    queen = Queen(rig.deps)
    [task] = await rig.deps.chamber.submit(make_graph_draft({"root": ()}))
    await dispatch_ready(rig.deps, (rig.stand,))
    job = rig.deps.dispatch.provisions.jobs[task.id].job

    stopping = asyncio.ensure_future(queen.stop())
    await _wait_until(lambda: rig.deps.dispatch.provisions.closed)
    awaited = not stopping.done()  # Held open by the acquisition, never cancelling it.
    rig.provider.land.set()
    await stopping
    await rig.deps.chamber.submit(make_graph_draft({"late": ()}))
    await dispatch_ready(rig.deps, (rig.stand,))

    assert awaited
    assert job.done() and not job.cancelled()
    assert len(rig.provider.calls) == 1  # A stopping Queen started nothing more.


def _heartbeat(tokens_used: int) -> Heartbeat:
    """One idle Warden's Heartbeat, its telemetry tagged with `tokens_used`."""
    return Heartbeat(
        telemetry=make_telemetry(tokens_used=tokens_used),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=(),
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


def _handled_by(
    queen: Queen, warden_id: WardenId, tokens_used: int
) -> Callable[[], Awaitable[bool]]:
    """A condition: the Heartbeat tagged `tokens_used` is the newest `warden_id` one she handled."""

    async def handled() -> bool:
        try:
            return (await queen.telemetry(warden_id)).tokens_used == tokens_used
        except UnknownWardenError:
            return False  # No Heartbeat handled yet.

    return handled


async def _is_running(deps: QueenDeps, task: Task) -> bool:
    """True once the task runs on its Cell."""
    return await _status(deps, task) is TaskStatus.RUNNING
