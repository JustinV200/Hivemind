"""Build the House Bee's Honey fixtures: a real SQLite Honey Store's handles, and fake Cell records.

The House Bee (the maintenance role) deposits aged Bee Bread and retired Cell Wax into the Honey
Store (the Hive's searchable knowledge base) and ripens it there; the Queen's pre-check and her
outcome deposit use the same handles. `open_honey_access` builds those handles (`HoneyAccess`)
over a fresh temp-file store with no model bound (heuristic summaries, full-text search), which
is everything a unit test of a deposit, a drain or a pre-check needs; a test that wants vectors
passes an embedder. `RecordedCells` is an honest in-memory `CellRecords`: the caller says which
Cell each task ran on and which Cells have live records, exactly the facts the Queen's own
implementation reads from her chamber and her Warden links. The sweep helpers
(`memory_context`, `sweep_deps`, `sweep_window`, `store_bee_bread`, `transcript_entry`,
`retire_wax`) build the memory-side half of a House Bee sweep the way every sweep test needs it.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    packages/hivemind/tests/unit/workers/roles/house_bee/**, tests/unit/queen/** (the pre-check,
    housekeeping and outcome tests) and tests/e2e/test_honey_compounds.py.

Key invariants:
    - Every store here is real SQLite (codingrules 14.4: fakes over mocks, real stores where they
      are cheap), built exactly as `builders.honey.open_test_honey_store_with_trail` builds one.
    - `RecordedCells` never invents a record: an unknown Cell is `GatheredOn.unrecorded`, the
      same conservative answer the Queen gives.

See Also:
    - hivemind.workers.roles.house_bee.honey for CellRecords, GatheredOn and HouseBeeHoney.
    - hivemind.honey_store.access for HoneyAccess, what `open_honey_access` builds.
    - tests/builders/honey.py for the store-level builders this module composes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from builders.honey import make_honey_identity, open_test_honey_store_with_trail
from builders.memory import make_bee_bread_entry, make_wax_proposal_input

from hivemind.cell import Cell, HoneyClearance
from hivemind.honey_store import (
    HoneyAccess,
    HoneyRetriever,
    HoneyStore,
    NectarIntake,
    RetrieverDeps,
    Ripener,
    RipenerDeps,
)
from hivemind.llm import BoundEmbedder, DirectEmbedGate
from hivemind.manifest import (
    HoneyClearanceSection,
    HoneyRetrievalSection,
    HoneyRipeningSection,
    HoneyStoreSection,
)
from hivemind.memory import (
    BeeBread,
    BeeBreadEntry,
    BeeBreadEntryKind,
    CellWax,
    MemoryContext,
    MemoryIdentity,
)
from hivemind.memory.cell_wax import clear_wax, expire_wax, propose_wax, write_wax
from hivemind.memory.store.memory import InMemoryMemoryStore
from hivemind.pheromone import MemoryEvent, MemoryPheromoneTrail, SqlitePheromoneTrail
from hivemind.workers.roles.house_bee import GatheredOn, HouseBeeHoney, SweepDeps, SweepWindow
from waggle.clock import Clock
from waggle.ids import (
    CellId,
    TaskId,
    new_cell_id,
    new_event_id,
    new_hive_id,
    new_node_id,
    new_task_id,
    new_worker_id,
)
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages import HoneyClearance as WireHoneyClearance
from waggle.messages.cell.wax import WaxDecision
from waggle.messages.honey import HoneyHit, HoneyProvenance

SWEEP_HOT_WINDOW = timedelta(hours=4)  # The manifest's default hot window.
WAX_TEXT_CAP = 4_000  # A generous [memory] wax text cap for proposals in tests.

__all__ = [
    "SWEEP_HOT_WINDOW",
    "WAX_TEXT_CAP",
    "HoneyHarness",
    "RecordedCells",
    "honey_access_over",
    "make_retrieved_hit",
    "memory_context",
    "open_honey_access",
    "retire_wax",
    "store_bee_bread",
    "sweep_deps",
    "sweep_window",
    "transcript_entry",
]


@dataclass(frozen=True, slots=True)
class HoneyHarness:
    """A real Honey Store's handles plus the trail its events land on, for one test."""

    access: HoneyAccess  # Intake, retriever, Ripener and store over one temp SQLite file.
    trail: SqlitePheromoneTrail  # The same connection's trail: every honey.* event is here.


async def open_honey_access(
    tmp_path: Path,
    clock: Clock,
    *,
    embedder: BoundEmbedder | None = None,
    retrieval: HoneyRetrievalSection | None = None,
    ripening: HoneyRipeningSection | None = None,
) -> HoneyHarness:
    """Open a fresh temp-file Honey Store and build its handles, with no ripener bound.

    Args:
        tmp_path: A writable directory for the database file.
        clock: Shared by the store, its trail and every handle.
        embedder: An EMBEDDER binding for vector search and embedding; None searches full text.
        retrieval: `[honey.retrieval]`; the manifest defaults when omitted.
        ripening: `[honey.ripening]`; the manifest defaults when omitted.

    Returns:
        The handles and the trail they record to.
    """
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    access = honey_access_over(
        store, clock, embedder=embedder, retrieval=retrieval, ripening=ripening
    )
    return HoneyHarness(access=access, trail=trail)


def honey_access_over(
    store: HoneyStore,
    clock: Clock,
    *,
    embedder: BoundEmbedder | None = None,
    retrieval: HoneyRetrievalSection | None = None,
    ripening: HoneyRipeningSection | None = None,
) -> HoneyAccess:
    """Build `HoneyAccess` over an already-open store, the way `build_honey_access` composes it.

    Args:
        store: The Honey Store every handle reads and writes.
        clock: Shared by every handle.
        embedder: An EMBEDDER binding, or None for full-text search only.
        retrieval: `[honey.retrieval]`; defaults when omitted.
        ripening: `[honey.ripening]`; defaults when omitted.

    Returns:
        A HoneyAccess with intake, a retriever and a Ripener (heuristic summaries) over `store`.
    """
    identity = make_honey_identity(clock)
    retrieval_section = retrieval if retrieval is not None else HoneyRetrievalSection()
    ripening_section = ripening if ripening is not None else HoneyRipeningSection()
    clearance = HoneyClearanceSection()
    gate = DirectEmbedGate() if embedder is not None else None
    default_label = HoneyClearance.from_wire(clearance.default_label)
    return HoneyAccess(
        store=store,
        intake=NectarIntake(store, identity, clock, HoneyStoreSection(), default_label),
        retriever=HoneyRetriever(
            RetrieverDeps(store, identity, clock, retrieval_section, embedder, gate)
        ),
        ripener=Ripener(
            RipenerDeps(
                store, identity, clock, ripening_section, embedder=embedder, embed_gate=gate
            )
        ),
        identity=identity,
        retrieval=retrieval_section,
        ripening=ripening_section,
        clearance=clearance,
    )


def make_retrieved_hit(clock: Clock, **overrides: object) -> HoneyHit:
    """Build a valid C1 hit in the `hive` scope, as a consultation of the Honey Store returns one.

    Args:
        clock: Source of the provenance ids and observed-at time.
        **overrides: Field values that replace the defaults below.

    Returns:
        A validated HoneyHit.
    """
    fields: dict[str, object] = {
        "honey_ref": "/hive/honey_01hittest0000000000000000",
        "title": "Where the widget service listens",
        "excerpt": "The widget service listens on port 48213.",
        "score": 0.8,
        "scope": "hive",
        "clearance": WireHoneyClearance.C1,
        "origin_tier": WireCombShieldLevel.MEADOW,
        "provenance": HoneyProvenance(
            task_id=new_task_id(clock),
            cell_id=new_cell_id(clock),
            bee=new_worker_id(clock),
            observed_at=clock.now(),
        ),
    }
    fields.update(overrides)
    return HoneyHit(**fields)


@dataclass
class RecordedCells:
    """An in-memory `CellRecords`: which Cell each task ran on, and which Cells have live records.

    Mutable on purpose (a test adds a placement or a record as it goes), like every fake here.
    """

    cells: dict[CellId, GatheredOn] = field(default_factory=dict)  # Live records, by Cell id.
    task_cells: dict[TaskId, CellId] = field(default_factory=dict)  # Where each task ran.
    home: GatheredOn | None = None  # Where task-less material was written; None skips it.

    @classmethod
    def of(
        cls,
        *cells: Cell,
        home: Cell | None = None,
        placements: Mapping[TaskId, CellId] | None = None,
    ) -> RecordedCells:
        """Build records from live Cells, an optional home Cell and task placements.

        Args:
            *cells: Cells with a live record.
            home: The Cell task-less material is attributed to; also recorded live.
            placements: Which Cell each task ran on.

        Returns:
            The records.
        """
        recorded = {cell.id: GatheredOn.of(cell) for cell in (*cells, *filter(None, (home,)))}
        return cls(
            cells=recorded,
            task_cells=dict(placements or {}),
            home=GatheredOn.of(home) if home is not None else None,
        )

    async def gathered_on(self, task_id: TaskId | None) -> GatheredOn | None:
        """Return the task's Cell (or the home Cell for task-less or unplaced material)."""
        if task_id is None or task_id not in self.task_cells:
            return self.home
        return self.for_cell(self.task_cells[task_id])

    def for_cell(self, cell_id: CellId) -> GatheredOn:
        """Return the Cell's live record, else the conservative unrecorded answer."""
        return self.cells.get(cell_id, GatheredOn.unrecorded(cell_id))


def memory_context(clock: Clock) -> MemoryContext:
    """Build a memory context over a fresh in-memory store, as every sweep test uses."""
    trail = MemoryPheromoneTrail(clock)
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )
    return MemoryContext(store=InMemoryMemoryStore(trail), identity=identity, clock=clock)


def sweep_deps(ctx: MemoryContext, access: HoneyAccess | None, records: RecordedCells) -> SweepDeps:
    """Build a SweepDeps with no RIPENER binding, depositing into `access` when it is given."""
    honey = HouseBeeHoney(access=access, cells=records) if access is not None else None
    return SweepDeps(memory=ctx, bee_bread=BeeBread(ctx.store), honey=honey)


def sweep_window(clock: Clock) -> SweepWindow:
    """A C2 sweep at the clock's current time, with the default hot window."""
    return SweepWindow(now=clock.now(), hot_window=SWEEP_HOT_WINDOW, allowance=HoneyClearance.C2)


async def store_bee_bread(ctx: MemoryContext, entry: BeeBreadEntry) -> None:
    """Store one Bee Bread entry directly, with the event the store requires beside it."""
    event = MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor="system",
        kind="memory.bee_bread_deposited",
        subject_id=entry.id,
        payload={},
    )
    await ctx.store.add_bee_bread_entry(entry, event)


def transcript_entry(clock: Clock, task_id: TaskId | None, text: str) -> BeeBreadEntry:
    """A TRANSCRIPT Bee Bread entry carrying `text` as its payload, written now."""
    return make_bee_bread_entry(
        clock=clock,
        kind=BeeBreadEntryKind.TRANSCRIPT,
        ref_ids=(),
        task_id=task_id,
        text=None,
        payload=text,
    )


async def retire_wax(
    ctx: MemoryContext, cell_id: CellId, *, proposer: str, expire: bool = False
) -> CellWax:
    """Propose, write, then clear (or let expire) one CAUTION about `cell_id`; return it retired."""
    expires_at = ctx.clock.now() + timedelta(hours=1) if expire else None
    inputs = make_wax_proposal_input(
        clock=ctx.clock, cell_id=cell_id, proposer=proposer, expires_at=expires_at
    )
    proposed = await propose_wax(inputs, WAX_TEXT_CAP, ctx)
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "Within the cap.", ctx)
    if expire:
        return await expire_wax(written, ctx)
    return await clear_wax(written, "The disk was replaced.", ctx)
