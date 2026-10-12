"""Build a small Hive for the Entrance's screen reads: a Queen's stores, a census and a board.

The reads under ``hivemind.entrance.reads.screens`` take ``HiveReads``: the Queen's stores, her
attached Wardens and pulse, and the telemetry board. ``scene`` builds those over the in-memory
fakes ``make_queen_deps`` provides, with one attached Warden on a Real Cell (or the Cell the test
passes), and offers the few writes a test needs to populate it: submit a task graph and place or
finish a task through the real Brood Chamber, record a trail event or a Cell Wax note, and feed the
board a Heartbeat.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    packages/hivemind/tests/unit/entrance/reads/screens and .../routes/screens.

Key invariants:
    - Everything is a real in-memory store; nothing is mocked.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from builders.cells import make_cell
from builders.observation import make_event
from builders.queen import make_queen_deps
from builders.supervision import make_telemetry
from builders.tasks import make_graph_draft, make_outcome

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CellKind
from hivemind.entrance.gate import HiveReads, LlmReads
from hivemind.entrance.gate.reads import ForageMapReads, VirtualCellCensus
from hivemind.entrance.streams import TelemetryBoard
from hivemind.forage import ModelSource
from hivemind.hive import LiveVirtualCell
from hivemind.memory import CellWax
from hivemind.pheromone import PheromoneEvent
from hivemind.queen import QueenDeps, WardenLink, WardenLiveness
from waggle.clock import FakeClock
from waggle.ids import WardenId, new_worker_id
from waggle.messages.supervision import ChildTelemetry, Heartbeat, WardenState, WorkerState

__all__ = ["Census", "Lifecycle", "Scene", "StaticMap", "heartbeat", "scene"]


@dataclass(frozen=True, slots=True)
class Census:
    """The Queen's attached Wardens and their pulse, as the census reads them."""

    wardens: tuple[WardenLink, ...]
    liveness: Mapping[WardenId, WardenLiveness] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Lifecycle:
    """The Virtual Cell lifecycle's live table."""

    rows: tuple[LiveVirtualCell, ...]

    def live_cells(self) -> tuple[LiveVirtualCell, ...]:
        """Every row, as the lifecycle lists them."""
        return self.rows


@dataclass(frozen=True, slots=True)
class StaticMap:
    """A Forage map that lists the sources it was given."""

    held: tuple[ModelSource, ...]

    def sources(self) -> tuple[ModelSource, ...]:
        """Every source it holds."""
        return self.held


@dataclass(frozen=True, slots=True)
class Scene:
    """A small Hive an Entrance read can run over, and the handles a test populates it through."""

    deps: QueenDeps
    link: WardenLink
    reads: HiveReads
    board: TelemetryBoard
    clock: FakeClock

    async def record(self, event: PheromoneEvent) -> None:
        """Record one trail event."""
        await self.deps.trail.record(event)

    async def submit(self, keys_and_deps: Mapping[str, tuple[str, ...]]) -> tuple[Task, ...]:
        """Submit a task graph (draft key to the keys it depends on) to the Brood Chamber."""
        return await self.deps.chamber.submit(make_graph_draft(keys_and_deps))

    async def place(self, task: Task) -> Task:
        """Assign a pending task to the Scene's Warden and Cell, and start it."""
        cell = self.link.cell
        await self.deps.chamber.assign(
            task.id, self.link.warden_id, cell.id, "test", bound_tier=cell.comb_shield
        )
        return await self.deps.chamber.start(task.id)

    async def finish(self, task: Task) -> Task:
        """Complete a running task, verified by the Scene's Warden."""
        outcome = make_outcome(verified_by=self.link.warden_id, clock=self.clock)
        return await self.deps.chamber.complete(task.id, outcome)

    async def write_wax(self, wax: CellWax) -> None:
        """Store a Cell Wax note with its trail event."""
        await self.deps.memory.put_wax(wax, make_event("memory.wax_proposed", wax.cell_id))  # type: ignore[arg-type]


def heartbeat(sub_bees: Sequence[tuple[str, WorkerState]] = (), **telemetry: object) -> Heartbeat:
    """An ACTIVE Warden's Heartbeat listing the given sub-bees (task id or none, and state).

    Args:
        sub_bees: One (task id, state) pair per sub-bee, with fresh Worker ids.
        **telemetry: Overrides for the Warden's own telemetry.

    Returns:
        The Heartbeat.
    """
    clock = FakeClock()
    children = tuple(
        ChildTelemetry(
            worker_id=new_worker_id(clock),
            task_id=task_id or None,
            state=state,
            telemetry=make_telemetry(),
        )
        for task_id, state in sub_bees
    )
    return Heartbeat(
        telemetry=make_telemetry(**telemetry),
        task_id=None,
        worker_state=None,
        warden_state=WardenState.ACTIVE,
        children=children,
        grant_id=None,
        grant_spend=None,
        interval_s=5.0,
    )


def scene(
    clock: FakeClock | None = None,
    *,
    cell: Cell | None = None,
    lifecycle: VirtualCellCensus | None = None,
    forage_map: ForageMapReads | None = None,
) -> Scene:
    """Build a Hive with one attached Warden and the reads the Entrance would have over it.

    Args:
        clock: Shared by every store; a fresh FakeClock when omitted.
        cell: The Warden's Cell; a fresh Real Cell when omitted.
        lifecycle: The Virtual Cell lifecycle's table; None for a Hive with no Virtual side.
        forage_map: The Forage map's sources; None leaves every Cell's hosting unknown.

    Returns:
        The scene.
    """
    active = clock if clock is not None else FakeClock()
    deps, link, _ = make_queen_deps(active, cell=cell or make_cell(CellKind.REAL, active))
    board = TelemetryBoard()
    reads = HiveReads(
        goal_requests=deps.goal_requests,
        chat=deps.chat,
        chamber=deps.chamber,
        trail=deps.trail,
        memory=deps.memory,
        ledger=deps.ledger,
        census=Census((link,)),
        telemetry=board,
        llm=LlmReads({}, deps.bindings, deps.cluster_state, deps.health_poller),
        virtual_cells=lifecycle,
        forage_map=forage_map,
    )
    return Scene(deps, link, reads, board, active)
