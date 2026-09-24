"""End-to-end tests for phase 7's exit criteria and ADR-0034: knowledge compounds through Honey.

Roadmap phase 7's first two exit criteria, run for real. (a) A goal run twice on the Hive Stand
(the machine the Queen runs on, a Real Cell, so borrowed) at C2: the first run's Drone has to
discover the fact it needs with a command, the Queen deposits the verified outcome into the Honey
Store (the Hive's searchable knowledge base), one House Bee pass turns it into Honey, and the
second run's `TaskAssign` carries it, `queen.honey_consulted` is on the trail, and the second
Drone goes straight to the answer in fewer model calls. Phase 6's Forager does not exist yet, so a
Drone stands in for it. (b) The same at C1 attaches nothing from the first run: everything
gathered on the borrowed Hive Stand is labelled C2 at intake, and a C1 task may not read C2 (ADR-
0031's "the rule working as written"); the Ripener records no reading of that outcome, so nothing
is proposed for lowering either. (c) A Handoff written through `memory.write_checkpoint` (phase
4's path) is, a day later, deposited by the House Bee's sweep on the Queen's own housekeeping
tick, ripened, and returned by a query with its full provenance.

ADR-0034's judge-reviewed label lowering, run the same way at C1, the default. (d) The Ripener
reads the first run's outcome as C1, the House Bee's pass files a proposal and the clearance judge
(the JUDGE slot) approves it, so the second run's `TaskAssign` carries the first run's outcome,
lowered to C1, and the trail holds `honey.lowering_proposed` and `honey.label_lowered` with
approver JUDGE. (e) The same with the judge rejecting: nothing from the first run is attached,
`honey.lowering_rejected` is on the trail, and `hive honey review` lists the proposal REJECTED.

(a), (b), (d) and (e) build a whole Hive with `hivemind.cli.compose.build_hive` over a real Hive
Stand lease, real SQLite and the real Queen, Warden, Drones and House Bee, every model call
answered by one scripted `FakeLLMProvider` (`tests.e2e.honey_runs.CompoundScript`). (c) drives the
Queen's housekeeping tick and one ripening pass directly over real SQLite stores on a `FakeClock`,
so a day passes in one call.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 7's exit criteria.
    - docs/adr/0031-honey-store-sqlite-fts5-sqlite-vec.md for the labels and scopes asserted here.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for (d) and (e).
    - tests.e2e.honey_runs for the scripted Hive every whole-Hive scenario here runs twice.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from builders.cli import ManifestTuning, fake_manifest
from builders.memory import make_handoff
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft
from e2e.honey_runs import (
    DISCOVERY_CALLS,
    INFORMED_CALLS,
    PORT,
    CompoundScript,
    capture_assignments,
    consulted,
    from_first_run,
    retrieved_port,
    run_twice,
    scripted_hive,
)
from structlog.testing import capture_logs
from typer.testing import CliRunner

from hivemind.brood_chamber import ChamberIdentity, TaskOutcome, TaskStatus
from hivemind.cell import Cell, HoneyClearance
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.app import app
from hivemind.cli.compose import Hive
from hivemind.cli.compose.deps import (
    build_fanner,
    build_hive_stand_source,
    build_provider_registry,
)
from hivemind.cli.compose.honey import build_honey_access
from hivemind.cli.stores import (
    build_forage_map,
    open_chamber,
    open_honey_store,
    open_memory,
    open_trail,
)
from hivemind.honey_store import (
    HoneyAccess,
    HoneyReader,
    HoneySearch,
    ReadFilter,
    queen_read_capabilities,
)
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.state import ClusterState
from hivemind.queen.ticks.housekeeping import run_housekeeping
from waggle.clock import FakeClock
from waggle.ids import TaskId, WorkerId, new_worker_id
from waggle.messages.honey import HoneyHit

pytestmark = pytest.mark.e2e

_DAY = timedelta(days=1)
# Every text deposit is summarised on the RIPENER slot (the default skips one under 400
# characters, a short verified outcome among them), so the Ripener records its own reading.
_SUMMARISED = ManifestTuning(summarise_min_chars=1)


# ──────────────────────────────────────────────────────────────────────────────
# (a) and (b): the same goal twice, at C2 and at C1
# ──────────────────────────────────────────────────────────────────────────────


# "none" is ProviderCapabilities.none(): prompted tool calls and an 8,192-token window, where the
# Drone's own output reserve once left no room for a single retrieved hit.
@pytest.mark.parametrize("capabilities", ["full", "none"])
def test_a_goal_run_twice_at_c2_compounds_through_honey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capabilities: str
) -> None:
    """(a) Roadmap phase 7 exit criterion 1: the second TaskAssign carries Honey; fewer steps."""
    script = CompoundScript("C2")
    assignments = capture_assignments(monkeypatch)
    hive = scripted_hive(tmp_path, script, capabilities)

    runs = asyncio.run(run_twice(hive, script, HoneyClearance.C2))

    assert runs.first.succeeded and runs.second.succeeded, (runs.first, runs.second)
    carried = from_first_run(assignments, runs)
    assert carried, "the second run's TaskAssign carried nothing from the first run's outcome"
    assert any(PORT in hit.excerpt for hit in carried)
    (consulted_event,) = asyncio.run(consulted(hive, runs.second.tasks[0].id))
    hits = consulted_event.payload["hits"]
    assert isinstance(hits, int) and hits >= 1
    assert runs.first_calls == DISCOVERY_CALLS
    assert runs.second_calls == INFORMED_CALLS
    assert runs.second_calls < runs.first_calls
    # The planner was shown it too (roadmap 7.9's "the planner queries Honey for the goal").
    assert retrieved_port(script.plan_prompts[-1]) == PORT
    assert retrieved_port(script.plan_prompts[0]) is None


def test_the_same_goal_at_c1_attaches_nothing_from_the_hive_stands_c2_honey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) Gathered on the borrowed Hive Stand, the outcome is C2; a C1 task may not read it."""
    script = CompoundScript("C1")
    assignments = capture_assignments(monkeypatch)
    hive = scripted_hive(tmp_path, script)

    runs = asyncio.run(run_twice(hive, script, HoneyClearance.C1))

    assert runs.first.succeeded and runs.second.succeeded, (runs.first, runs.second)
    assert from_first_run(assignments, runs) == []
    assert runs.second_calls == runs.first_calls == DISCOVERY_CALLS
    (consulted_event,) = asyncio.run(consulted(hive, runs.second.tasks[0].id))
    withheld = consulted_event.payload["withheld"]
    assert isinstance(withheld, int) and withheld >= 1  # Found, and withheld by clearance.
    assert asyncio.run(_outcome_labels(hive)) == {HoneyClearance.C2}
    # No Ripener reading of the outcome was recorded, so nothing was proposed for lowering.
    assert asyncio.run(_events(hive, "honey.lowering_proposed")) == []


async def _outcome_labels(hive: Hive) -> set[HoneyClearance]:
    """The labels of every Honey row in the `hive` scope, read with no ceiling below C2."""
    assert hive.honey is not None
    everything = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)
    rows = await hive.honey.store.list_honey(everything, scope_prefix="hive", limit=50, offset=0)
    return {row.clearance for row in rows}


async def _events(hive: Hive, kind: str) -> list[PheromoneEvent]:
    """Every event of `kind` on the Hive's own trail, oldest first."""
    return list(await hive.stores.trail.query(TrailQuery(kind=kind)))


# ──────────────────────────────────────────────────────────────────────────────
# (d) and (e): at C1, the default, the clearance judge decides whether the outcome is lowered
# ──────────────────────────────────────────────────────────────────────────────


def test_d_at_c1_a_judge_approved_lowering_lets_the_second_run_read_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(d) ADR-0034: the Ripener reads the outcome as C1, the judge approves, C1 reads it."""
    script = CompoundScript("C1", reading="C1", verdict="APPROVE")
    assignments = capture_assignments(monkeypatch)
    hive = scripted_hive(tmp_path, script, tuning=_SUMMARISED)

    runs = asyncio.run(run_twice(hive, script, HoneyClearance.C1))

    assert runs.first.succeeded and runs.second.succeeded, (runs.first, runs.second)
    carried = from_first_run(assignments, runs)
    assert carried, "the lowered outcome did not reach the second run's TaskAssign"
    assert any(PORT in hit.excerpt for hit in carried)
    assert {hit.clearance.value for hit in carried} == {"C1"}  # Lowered from the floor's C2.
    assert runs.second_calls == INFORMED_CALLS
    assert len(script.clearance_reviews) == 1  # Asked once, about the one proposal.
    (proposed,) = asyncio.run(_events(hive, "honey.lowering_proposed"))
    (lowered,) = asyncio.run(_events(hive, "honey.label_lowered"))
    assert lowered.payload["approver"] == "JUDGE"
    assert (lowered.payload["from"], lowered.payload["to"]) == ("C2", "C1")
    assert lowered.subject_id == proposed.subject_id  # The same Nectar, proposed then lowered.


def test_e_at_c1_a_judge_rejection_keeps_the_first_outcome_from_the_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(e) ADR-0034: the judge rejects, so the outcome stays C2 and the C1 run discovers again."""
    script = CompoundScript("C1", reading="C1", verdict="REJECT")
    assignments = capture_assignments(monkeypatch)
    hive = scripted_hive(tmp_path, script, tuning=_SUMMARISED)

    runs = asyncio.run(run_twice(hive, script, HoneyClearance.C1))

    assert runs.first.succeeded and runs.second.succeeded, (runs.first, runs.second)
    assert from_first_run(assignments, runs) == []
    assert runs.second_calls == runs.first_calls == DISCOVERY_CALLS
    (rejected,) = asyncio.run(_events(hive, "honey.lowering_rejected"))
    assert (rejected.payload["approver"], rejected.payload["outcome"]) == ("JUDGE", "REJECT")
    assert asyncio.run(_events(hive, "honey.label_lowered")) == []
    # The operator's own view: `hive honey review` over the same file lists it REJECTED.
    (listed,) = _review_listing(tmp_path / "hive.toml")["proposals"]
    assert (listed["state"], listed["approver"], listed["to_label"]) == ("REJECTED", "JUDGE", "C1")


def _review_listing(manifest: Path) -> dict[str, list[dict[str, object]]]:
    """Run `hive honey review --json` against the Hive's own manifest; return its document."""
    # structlog's default writes to stdout; captured, it stays out of the JSON document.
    with capture_logs():
        result = CliRunner().invoke(app, ["honey", "--manifest", str(manifest), "review", "--json"])
    assert result.exit_code == 0, result.output
    listing: dict[str, list[dict[str, object]]] = json.loads(result.stdout)
    return listing


# ──────────────────────────────────────────────────────────────────────────────
# (c): a phase 4 Handoff, retrievable through Honey a day later with full provenance
# ──────────────────────────────────────────────────────────────────────────────


class _RealStores:
    """The Hive's own SQLite stores and Honey handles, opened the way a Hive opens them."""

    def __init__(self, manifest: HiveManifest, clock: FakeClock) -> None:
        db = manifest.resolve_path(manifest.hive.db)
        identity = ChamberIdentity(
            hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
        )
        self.trail = open_trail(db)
        self.chamber = open_chamber(db, identity)
        self.memory = open_memory(db)
        forage_map = build_forage_map(manifest, clock)
        registry = build_provider_registry(manifest, {}, clock, forage_map, None)
        fanner = build_fanner(manifest, forage_map, self.trail, clock)
        # The one way a Hive builds its Honey Store (hivemind.cli.compose.honey).
        self.honey: HoneyAccess = build_honey_access(
            manifest, open_honey_store(db), registry, fanner, clock
        )
        source = build_hive_stand_source(
            manifest, self.trail, clock, InMemoryLeavingsStore(self.trail)
        )
        self.stand: Cell = asyncio.run(source.cells())[0]


def _queen_over(stores: _RealStores, clock: FakeClock) -> tuple[QueenDeps, WardenLink]:
    """A Queen over the real stores, attached to a Warden on the real Hive Stand's own Cell."""
    deps, link, _end = make_queen_deps(
        clock,
        cell=stores.stand,
        chamber=stores.chamber,
        memory=stores.memory,
        trail=stores.trail,
        honey=stores.honey,
    )
    return deps, link


@dataclass(frozen=True, slots=True)
class _Checkpointed:
    """What scenario (c) wrote at t0, and what one ripening pass a day later made of it."""

    task_id: TaskId  # The task the Handoff concerns.
    worker: WorkerId  # The bee that wrote it.
    t0: datetime  # When it was written.
    ripened: int  # Nectar the pass ripened.


async def _handoff_a_day_later(
    deps: QueenDeps, link: WardenLink, clock: FakeClock
) -> _Checkpointed:
    """Checkpoint a task's Handoff at t0, finish the task, and sweep and ripen a day later."""
    (task,) = await deps.chamber.submit(make_graph_draft({"migrate": ()}))
    await deps.chamber.assign(task.id, link.warden_id, link.cell.id, "Placed for this scenario.")
    await deps.chamber.start(task.id)
    worker = new_worker_id(clock)
    handoff = make_handoff(
        task_id=task.id,
        written_by=worker,
        clearance=HoneyClearance.C1,
        goal="Migrate the ledger service to the new schema.",
        progress="The ledger migration script ran against staging; production is next.",
    )
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=clock)
    t0 = clock.now()
    # Phase 4's own path: store the Handoff, record memory.checkpoint, index it in Bee Bread.
    await write_checkpoint(handoff, task.id, ctx)
    # Finishing the task clears its placement: the sweep must still find where it ran.
    outcome = TaskOutcome(
        status=TaskStatus.SUCCEEDED, summary="Migrated.", verified_by=link.warden_id
    )
    await deps.chamber.complete(task.id, outcome)
    state = ClusterState()
    await run_housekeeping(deps, [link], state)  # The first tick only seeds the sweep timer.
    clock.advance((_DAY + timedelta(hours=1)).total_seconds())
    await run_housekeeping(deps, [link], state)  # Due: the House Bee's sweep runs.
    assert deps.honey is not None
    passed = await deps.honey.ripener.run_pass()
    return _Checkpointed(task.id, worker, t0, passed.ripen.ripened)


async def _query(access: HoneyAccess, deps: QueenDeps, text: str) -> list[HoneyHit]:
    """Ask the Honey Store as the Queen reads: every scope, up to C2."""
    reader = HoneyReader(
        requester=deps.identity.hive_id,
        capabilities=queen_read_capabilities(),
        ceiling=HoneyClearance.C2,
        is_night_veil=False,
    )
    response = await access.retriever.search(HoneySearch(text, reader, (), 5, 4_000))
    return list(response.hits)


def test_a_phase_four_handoff_is_retrievable_a_day_later_with_full_provenance(
    tmp_path: Path,
) -> None:
    """(c) Roadmap phase 7 exit criterion 2, over real SQLite stores on a FakeClock."""
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock), {})
    stores = _RealStores(manifest, clock)
    deps, link = _queen_over(stores, clock)

    async def scenario() -> tuple[_Checkpointed, list[HoneyHit]]:
        written = await _handoff_a_day_later(deps, link, clock)
        return written, await _query(stores.honey, deps, "ledger migration schema staging")

    written, hits = asyncio.run(scenario())

    assert written.ripened == 1
    found = [hit for hit in hits if "ledger" in hit.excerpt.lower()]
    assert found, "the Handoff was not found through Honey a day later"
    provenance = found[0].provenance
    assert provenance.task_id == written.task_id
    assert provenance.cell_id == stores.stand.id  # Where its task ran: the Hive Stand.
    assert provenance.bee == written.worker
    assert provenance.observed_at == written.t0
    assert found[0].scope == f"task:{written.task_id}"  # A task's working material.
    # Declared C1, gathered on the borrowed Hive Stand: intake's floor made it C2.
    assert found[0].clearance.value == "C2"
