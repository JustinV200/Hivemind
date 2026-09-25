"""End-to-end tests for phase 7's exit criteria: knowledge compounds across runs through Honey.

Roadmap phase 7's first two exit criteria, run for real. (a) A goal run twice on the Hive Stand
(the machine the Queen runs on, a Real Cell, so borrowed) at C2: the first run's Drone has to
discover the fact it needs with a command, the Queen deposits the verified outcome into the Honey
Store (the Hive's searchable knowledge base), one House Bee ripening pass turns it into Honey, and
the second run's `TaskAssign` carries it, `queen.honey_consulted` is on the trail, and the second
Drone goes straight to the answer in fewer model calls. Phase 6's Forager does not exist yet, so a
Drone stands in for it. (b) The same at C1 attaches nothing from the first run: everything
gathered on the borrowed Hive Stand is labelled C2 at intake, and a C1 task may not read C2 (ADR-
0035's "the rule working as written"). (c) A Handoff written through `memory.write_checkpoint`
(phase 4's path) is, a day later, deposited by the House Bee's sweep on the Queen's own
housekeeping tick, ripened, and returned by a query with its full provenance.

(a) and (b) build a whole Hive with `hivemind.cli.compose.build_hive` over a real Hive Stand lease,
real SQLite and the real Queen, Warden and Drones, every model call answered by one scripted
`FakeLLMProvider` (`_CompoundScript`). (c) drives the Queen's housekeeping tick and one ripening
pass directly over real SQLite stores on a `FakeClock`, so a day passes in one call.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 7's exit criteria.
    - docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md for the labels and scopes asserted here.
    - tests.e2e.kernel_helpers for the scripting primitives reused here.
"""

from __future__ import annotations

import asyncio
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.memory import make_handoff
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft
from e2e.kernel_helpers import (
    judge_approve_response,
    plan_response,
    text_response,
    tool_response,
    tool_round_count,
    wait_until,
    write_call,
)

from hivemind.brood_chamber import ChamberIdentity, TaskOutcome, TaskStatus
from hivemind.cell import Cell, HoneyClearance
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.compose import GoalReport, Hive, build_hive, run_goal, run_hive
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
from hivemind.forage.slots import ModelSlot
from hivemind.honey_store import (
    HoneyAccess,
    HoneyReader,
    HoneySearch,
    ReadFilter,
    queen_read_capabilities,
)
from hivemind.llm import LLMRequest, LLMResponse, TextPart, ToolResultPart
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.memory import MemoryContext, write_checkpoint
from hivemind.pheromone import PheromoneEvent, TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.state import ClusterState
from hivemind.queen.ticks.housekeeping import run_housekeeping
from waggle.clock import FakeClock, SystemClock
from waggle.codec import Codec
from waggle.envelope import Envelope
from waggle.ids import TaskId, WorkerId, new_worker_id
from waggle.messages.honey import HoneyHit
from waggle.messages.task import TaskAssign

pytestmark = pytest.mark.e2e

_GOAL = "Find which port the widget service listens on, and write it down."
_OBJECTIVE = (
    "Find which port the widget service listens on and write that port number to answer.txt."
)
_PORT = "48213"
_FACT_TEXT = f"The widget service listens on port {_PORT}."
_FACT_RE = re.compile(r"widget service listens on port (\d+)")
_DISCOVERY_FILE = "widget_service.txt"
_ANSWER_FILE = "answer.txt"
# The discovery command: what a Drone has to run when nothing it was handed says the port. The
# command writes its finding to scratch, since run_command reports only the Capping verdict.
_DISCOVER = f"open({_DISCOVERY_FILE!r}, 'w').write({_FACT_TEXT!r})"
_RETRIEVED_OPEN = "\n<<<retrieved>>>\n"  # The rendered section's own delimiter lines.
_RETRIEVED_CLOSE = "\n<<<end retrieved>>>"
_TIMEOUT_S = 20.0  # Generous: each run finishes in well under two seconds on this host.
_DISCOVERY_CALLS = 4  # Run the command, read its file, write the answer, close.
_INFORMED_CALLS = 2  # Write the answer, close.
_DAY = timedelta(days=1)


# ──────────────────────────────────────────────────────────────────────────────
# The scripted Hive: one plan, and a Drone that discovers only what it was not told
# ──────────────────────────────────────────────────────────────────────────────


def _plan(clearance: str) -> dict[str, object]:
    """The one-task plan both runs share, at `clearance`."""
    return {
        "tasks": [
            {
                "key": "widget-port",
                "title": "Find the widget service's port",
                "objective": _OBJECTIVE,
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": _ANSWER_FILE, "argv": [], "expected": None}
                ],
                "needs": {},
                "clearance": clearance,
                "depends_on": [],
            }
        ]
    }


class _CompoundScript:
    """Answer every model call in the Hive: the plan, the Drone's rounds, and a judge's approval.

    The Drone's script depends only on what its own prompt holds: when the RETRIEVED section (the
    Honey the Queen's pre-check attached) already says the port, it writes the answer at once;
    otherwise it runs a discovery command, reads what it found, then writes the answer. Every
    WORKER-slot call is counted, and every QUEEN-slot (planning) prompt kept, for the assertions.
    """

    def __init__(self, clearance: str) -> None:
        self._plan = _plan(clearance)
        self.worker_calls = 0
        self.plan_prompts: list[str] = []

    def responder(self, request: LLMRequest) -> LLMResponse:
        """Route one request by its slot; the one Responder this module installs."""
        if request.slot is ModelSlot.QUEEN:
            self.plan_prompts.append(request.system or "")
            return plan_response(request, self._plan)
        if request.slot is ModelSlot.WORKER:
            self.worker_calls += 1
            return self._drone_turn(request)
        if request.slot is ModelSlot.JUDGE:
            return judge_approve_response(request)
        # The RIPENER's summaries fall back to heuristic ones on this answer, which is fine: the
        # verified outcome leads its own content, so the heuristic summary keeps the fact.
        return text_response("{}")

    def _drone_turn(self, request: LLMRequest) -> LLMResponse:
        """One Drone round: straight to the answer when told the port, else discover it first."""
        rounds = tool_round_count(request)
        told = _retrieved_port(request.system or "")
        if told is not None:
            return _informed_turn(request, rounds, told)
        return _discovering_turn(request, rounds)


def _informed_turn(request: LLMRequest, rounds: int, port: str) -> LLMResponse:
    """Told the port by the Honey it was handed: write the answer, then close."""
    if rounds == 0:
        return tool_response(request, (write_call(_ANSWER_FILE, port),))
    return text_response(f"The widget service listens on port {port}.")


def _discovering_turn(request: LLMRequest, rounds: int) -> LLMResponse:
    """Not told: run the discovery command, read what it wrote, write the answer, then close."""
    if rounds == 0:
        return tool_response(request, (_run_call(),))
    if rounds == 1:
        return tool_response(request, (_read_call(),))
    # What the read reported, as this attempt's own conversation carries it.
    port = _found_port(request) or "unknown"
    if rounds == 2:
        return tool_response(request, (write_call(_ANSWER_FILE, port),))
    return text_response(f"The widget service listens on port {port}.")


def _run_call() -> tuple[str, str, dict[str, object]]:
    """The discovery command, as a `run_command` call triple."""
    return ("discover", "run_command", {"argv": [sys.executable, "-c", _DISCOVER]})


def _read_call() -> tuple[str, str, dict[str, object]]:
    """Reading back what the discovery command found, as a `read_file` call triple."""
    return ("read", "read_file", {"path": _DISCOVERY_FILE})


def _retrieved_port(system: str) -> str | None:
    """Return the port the prompt's RETRIEVED section states, or None when it states none."""
    start = system.find(_RETRIEVED_OPEN)
    if start == -1:
        return None
    section = system[start : system.find(_RETRIEVED_CLOSE, start)]
    match = _FACT_RE.search(section)
    return match.group(1) if match else None


def _found_port(request: LLMRequest) -> str | None:
    """Return the port a tool result in this attempt's own conversation reported, if any."""
    # Native tool results arrive as ToolResultParts, prompted ones as text; either may carry it.
    for message in request.messages:
        for part in message.parts:
            text = part.content if isinstance(part, ToolResultPart) else None
            if isinstance(part, TextPart):
                text = part.text
            match = _FACT_RE.search(text or "")
            if match:
                return match.group(1)
    return None


def _capture_assignments(monkeypatch: pytest.MonkeyPatch) -> list[TaskAssign]:
    """Record every TaskAssign put on the wire (the Queen's, and the Warden's copy to its bee)."""
    captured: list[TaskAssign] = []
    original = Codec.encode

    def encode(self: Codec, envelope: Envelope) -> bytes:
        if isinstance(envelope.payload, TaskAssign):
            captured.append(envelope.payload)
        return original(self, envelope)

    monkeypatch.setattr(Codec, "encode", encode)
    return captured


def _hive(tmp_path: Path, script: _CompoundScript, capabilities: str = "full") -> Hive:
    """Build a whole Hive over the Hive Stand, real SQLite and `script`; outside any loop."""
    manifest = load_manifest(fake_manifest(tmp_path, capabilities=capabilities), {})
    return build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )


# ──────────────────────────────────────────────────────────────────────────────
# (a) and (b): the same goal twice, at C2 and at C1
# ──────────────────────────────────────────────────────────────────────────────


class _TwoRuns:
    """What running the goal twice produced: both reports and each run's Worker calls."""

    def __init__(self, first: GoalReport, second: GoalReport, calls: tuple[int, int]) -> None:
        self.first = first
        self.second = second
        self.first_calls, self.second_calls = calls


async def _run_twice(hive: Hive, script: _CompoundScript, clearance: HoneyClearance) -> _TwoRuns:
    """Run the goal, let the House Bee ripen what it left, and run the same goal again."""
    access = hive.honey
    assert access is not None, "build_hive wired no Honey Store"
    async with run_hive(hive):
        first = await run_goal(hive, _GOAL, clearance=clearance, timeout_s=_TIMEOUT_S)
        first_calls = script.worker_calls
        outcome_key = f"task_outcome:{first.tasks[0].id}"
        # The Queen deposits the outcome right after completing the task, a beat after run_goal
        # can already see it SUCCEEDED; wait for the deposit rather than race it.
        await wait_until(lambda: access.store.has_source(outcome_key))
        # The House Bee's own pass, driven here instead of waiting out its thirty-second pause.
        await access.ripener.run_pass()
        second = await run_goal(hive, _GOAL, clearance=clearance, timeout_s=_TIMEOUT_S)
    return _TwoRuns(first, second, (first_calls, script.worker_calls - first_calls))


def _from_first_run(assignments: list[TaskAssign], runs: _TwoRuns) -> list[str]:
    """Every attached hit's excerpt that came from the first run's own task, second run only."""
    first_task = runs.first.tasks[0].id
    second_task = runs.second.tasks[0].id
    return [
        hit.excerpt
        for assignment in assignments
        if assignment.task_id == second_task
        for hit in assignment.honey
        if hit.provenance.task_id == first_task
    ]


async def _consulted(hive: Hive, task_id: TaskId) -> list[PheromoneEvent]:
    """The `queen.honey_consulted` events of the pre-check for `task_id`."""
    events = await hive.stores.trail.query(
        TrailQuery(kind="queen.honey_consulted", subject_id=task_id)
    )
    return [event for event in events if event.payload.get("stage") == "assign"]


# "none" is ProviderCapabilities.none(): prompted tool calls and an 8,192-token window, where the
# Drone's own output reserve once left no room for a single retrieved hit.
@pytest.mark.parametrize("capabilities", ["full", "none"])
def test_a_goal_run_twice_at_c2_compounds_through_honey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capabilities: str
) -> None:
    """(a) Roadmap phase 7 exit criterion 1: the second TaskAssign carries Honey; fewer steps."""
    script = _CompoundScript("C2")
    assignments = _capture_assignments(monkeypatch)
    hive = _hive(tmp_path, script, capabilities)

    runs = asyncio.run(_run_twice(hive, script, HoneyClearance.C2))

    assert runs.first.succeeded and runs.second.succeeded, (runs.first, runs.second)
    carried = _from_first_run(assignments, runs)
    assert carried, "the second run's TaskAssign carried nothing from the first run's outcome"
    assert any(_PORT in excerpt for excerpt in carried)
    (consulted,) = asyncio.run(_consulted(hive, runs.second.tasks[0].id))
    hits = consulted.payload["hits"]
    assert isinstance(hits, int) and hits >= 1
    assert runs.first_calls == _DISCOVERY_CALLS
    assert runs.second_calls == _INFORMED_CALLS
    assert runs.second_calls < runs.first_calls
    # The planner was shown it too (roadmap 7.9's "the planner queries Honey for the goal").
    assert _retrieved_port(script.plan_prompts[-1]) == _PORT
    assert _retrieved_port(script.plan_prompts[0]) is None


def test_the_same_goal_at_c1_attaches_nothing_from_the_hive_stands_c2_honey(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """(b) Gathered on the borrowed Hive Stand, the outcome is C2; a C1 task may not read it."""
    script = _CompoundScript("C1")
    assignments = _capture_assignments(monkeypatch)
    hive = _hive(tmp_path, script)

    runs = asyncio.run(_run_twice(hive, script, HoneyClearance.C1))

    assert runs.first.succeeded and runs.second.succeeded, (runs.first, runs.second)
    assert _from_first_run(assignments, runs) == []
    assert runs.second_calls == runs.first_calls == _DISCOVERY_CALLS
    (consulted,) = asyncio.run(_consulted(hive, runs.second.tasks[0].id))
    withheld = consulted.payload["withheld"]
    assert isinstance(withheld, int) and withheld >= 1  # Found, and withheld by clearance.
    assert asyncio.run(_outcome_labels(hive)) == {HoneyClearance.C2}


async def _outcome_labels(hive: Hive) -> set[HoneyClearance]:
    """The labels of every Honey row in the `hive` scope, read with no ceiling below C2."""
    assert hive.honey is not None
    everything = ReadFilter(readable=("*",), max_clearance=HoneyClearance.C2)
    rows = await hive.honey.store.list_honey(everything, scope_prefix="hive", limit=50, offset=0)
    return {row.clearance for row in rows}


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
