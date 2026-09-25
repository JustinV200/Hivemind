"""End to end over Waggle: a Worker recalls Honey and remembers a finding through its Warden.

Roadmap step 7.8 in one in-process slice, every hop real: a Drone-shaped Worker runs inside a real
`WorkerRuntime`, spawned by a real `Warden` from a real `TaskAssign`; its `recall` and `remember`
tools go through its own Honey channel, the Warden's relay, and the Queen's real tick handler
(`hivemind.queen.ticks.liveness.handle_infrastructure_item`) into a real SQLite Honey Store that
already holds one ripened finding. The query round trip must come back with that finding inside
a delimited retrieved block, correlated hop by hop; the deposit, larger than one Waggle chunk,
must cross as exactly two chunks and land in intake whole, with the Worker's own provenance.

Fits into the Hive:
    Test only (codingrules section 14), under tests/e2e/. Wires hivemind.queen.ticks.honey,
    hivemind.wardens.ticks.honey, hivemind.workers.runtime.honey and hivemind.workers.tools.honey
    together over MemoryTransport pairs, the way a Hive Stand runs them in one process.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md step 7.8 for the behaviour proved here.
    - builders.honey_wire for the seeded store and the raw link helpers.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.honey import open_test_honey_store_with_trail
from builders.honey_wire import FINDING_QUERY, FINDING_TEXT, make_honey_access, seed_finding
from builders.queen import make_queen_deps
from builders.tasks import make_graph_draft
from builders.wardens import make_warden_deps
from builders.workers import ScriptedWorker, make_assignment, make_outcome

from hivemind.brood_chamber import Task
from hivemind.cell import Cell, CellKind
from hivemind.honey_store import SqliteHoneyStore
from hivemind.memory import Handoff
from hivemind.pheromone import SqlitePheromoneTrail
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.inbox import to_inbox_item
from hivemind.queen.ticks.liveness import handle_infrastructure_item
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools import ToolInvocation, recall, remember
from waggle.clock import FakeClock
from waggle.codec import Codec
from waggle.envelope import Envelope, Hop, wrap
from waggle.ids import new_grant_id
from waggle.messages.base import MAX_CHUNK_BYTES
from waggle.messages.forage import AllowedBinding, GrantIssued, SourceRef
from waggle.messages.forage.values import Effort as WireEffort
from waggle.messages.honey import HoneyQuery, NectarDeposit
from waggle.messages.labels import Postcondition, PostconditionKind
from waggle.messages.task import TaskAssign, TaskOutcome, TaskResult, WorkerRole
from waggle.transport.memory import MemoryTransport

pytestmark = pytest.mark.e2e

_SERVE_TIMEOUT_S = 10.0  # A real-time guard: the whole in-process slice runs in milliseconds.
_OUTPUT = "output.txt"  # The one artefact the task's acceptance checks for.
# A finding bigger than one Waggle chunk (MAX_CHUNK_BYTES), so it must cross as two.
_LONG_FINDING = "widgetd reloads its staging config on SIGHUP.\n" * (MAX_CHUNK_BYTES // 40)


@dataclass(frozen=True, slots=True)
class _Hive:
    """The Queen's side of the slice: her collaborators, her link to the Warden, her store."""

    deps: QueenDeps
    link: WardenLink
    store: SqliteHoneyStore
    trail: SqlitePheromoneTrail
    task: Task


@dataclass
class _WorkerSaw:
    """What the scripted Worker's own tool calls returned, for the assertions after the run."""

    recalled: str = ""
    remembered: str = ""


@dataclass(frozen=True, slots=True)
class _Run:
    """One run of the slice: every envelope the Queen handled, and what the Worker saw."""

    seen: list[Envelope]
    saw: _WorkerSaw


def _worker_factory(saw: _WorkerSaw) -> Callable[[WorkerRole], ScriptedWorker]:
    """Build a Worker that recalls, remembers a long finding, writes its output and claims."""

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        invocation = ToolInvocation(ctx=ctx, assignment=assignment)
        saw.recalled = await recall(invocation, {"query": FINDING_QUERY})
        saw.remembered = await remember(
            invocation, {"title": "How widgetd reloads", "text": _LONG_FINDING}
        )
        await ctx.session.put_file(Path(_OUTPUT), b"done")
        return make_outcome(summary="Recalled the config and remembered how it reloads.")

    def factory(role: WorkerRole) -> ScriptedWorker:
        return ScriptedWorker(script, role=role)

    return factory


async def _hive(tmp_path: Path, clock: FakeClock, cell: Cell) -> tuple[_Hive, MemoryTransport]:
    """Build the Queen's side over a seeded store; return it and the Warden's end of her link."""
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    access = make_honey_access(store, clock)
    await seed_finding(access, cell, clock)
    deps, standard_link, _end = make_queen_deps(clock, cell=cell, honey=access)
    queen_transport, warden_transport = MemoryTransport.pair(Codec(), Codec())
    link = WardenLink(
        warden_id=standard_link.warden_id,
        cell=cell,
        transport=queen_transport,
        hop=standard_link.hop,
    )
    (task,) = await deps.chamber.submit(make_graph_draft({"a": ()}))
    placed = await deps.chamber.assign(
        task.id, link.warden_id, cell.id, "the only Cell", bound_tier=cell.comb_shield
    )
    return _Hive(deps, link, store, trail, placed), warden_transport


def _orders(hive: _Hive, cell: Cell) -> tuple[GrantIssued, TaskAssign]:
    """The grant and assignment the Queen sends so the Warden spawns the Worker."""
    clock = hive.deps.clock
    grant = GrantIssued(
        grant_id=new_grant_id(clock),
        holder=hive.link.warden_id,
        cell_id=cell.id,
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
        max_sub_bees=1,
        expires_at=clock.now(),
        reason="the slice's grant",
    )
    assignment = make_assignment(
        clock=clock,
        task_id=hive.task.id,
        goal_id=hive.task.goal_id,
        cell_id=cell.id,
        grant_id=grant.grant_id,
        acceptance=(
            Postcondition(
                kind=PostconditionKind.FILE_EXISTS, subject=_OUTPUT, argv=(), expected=None
            ),
        ),
    )
    return grant, assignment


async def _serve(hive: _Hive) -> list[Envelope]:
    """Run the Queen's real tick handler on every envelope until the task's result arrives."""
    seen: list[Envelope] = []
    wardens = {hive.link.warden_id: hive.link}
    inbox = hive.link.transport.receive()
    async with asyncio.timeout(_SERVE_TIMEOUT_S):
        while not any(isinstance(envelope.payload, TaskResult) for envelope in seen):
            envelope = await anext(inbox)
            seen.append(envelope)
            item = to_inbox_item(envelope, hive.link.warden_id)
            await handle_infrastructure_item(hive.deps, wardens, item, {}, {})
    return seen


async def _run_slice(hive: _Hive, cell: Cell, warden_transport: MemoryTransport) -> _Run:
    """Start a real Warden on `cell`, send it the orders, and serve the Queen until the result."""
    clock = hive.deps.clock
    saw = _WorkerSaw()
    warden_hop = Hop(
        sender=hive.link.warden_id,
        recipient=hive.deps.identity.hive_id,
        node_id=hive.deps.identity.node_id,
    )
    deps, _queen_end, warden_id = make_warden_deps(
        clock,
        cells=(cell,),
        worker_factory=_worker_factory(saw),
        warden_id=hive.link.warden_id,
        queen_link=warden_transport,
        hop=warden_hop,
    )
    warden = Warden(warden_id, deps)
    await warden.start()
    run_task = asyncio.ensure_future(warden.run())
    # The Queen's two orders, over her own end of the link, exactly as her dispatcher sends them.
    for order in _orders(hive, cell):
        await hive.link.transport.send(wrap(order, hive.link.hop, clock=clock))
    seen = await _serve(hive)
    await warden.stop()
    await asyncio.wait_for(run_task, timeout=_SERVE_TIMEOUT_S)
    return _Run(seen=seen, saw=saw)


async def test_a_worker_recalls_honey_and_its_two_chunk_finding_reaches_intake(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    cell = make_cell(kind=CellKind.VIRTUAL, clock=clock)
    hive, warden_transport = await _hive(tmp_path, clock, cell)

    run = await _run_slice(hive, cell, warden_transport)

    # The query round trip: one query up, the seeded finding back inside the retrieved block.
    queries = [envelope for envelope in run.seen if isinstance(envelope.payload, HoneyQuery)]
    assert [query.sender for query in queries] == [hive.link.warden_id]
    assert "<<<retrieved>>>" in run.saw.recalled
    assert FINDING_TEXT.split(" and ")[0] in run.saw.recalled
    queried = await hive.trail.query(TrailQuery(kind="honey.queried"))
    assert len(queried) == 1
    worker_id = queried[0].subject_id
    assert worker_id.startswith("worker_")
    # The deposit: exactly two chunks up, one whole Nectar row in intake with the Worker's name.
    await _assert_deposit_reached_intake(hive, run, worker_id)
    # And the task itself still completed, verified by the Warden as ever.
    (result,) = [
        envelope.payload for envelope in run.seen if isinstance(envelope.payload, TaskResult)
    ]
    assert result.outcome is TaskOutcome.SUCCEEDED


async def _assert_deposit_reached_intake(hive: _Hive, run: _Run, worker_id: str) -> None:
    """Two chunks crossed the Queen's link, and intake holds the whole finding, as the Worker's."""
    chunks = [
        envelope.payload for envelope in run.seen if isinstance(envelope.payload, NectarDeposit)
    ]
    assert [(chunk.offset, chunk.final) for chunk in chunks] == [
        (0, False),
        (MAX_CHUNK_BYTES, True),
    ]
    content = _LONG_FINDING.encode("utf-8")
    (nectar,) = [
        row
        for row in await hive.store.pending_nectar(10)
        if row.sha256 == hashlib.sha256(content).hexdigest()
    ]
    assert (nectar.bee, nectar.task_id, nectar.cell_id) == (
        worker_id,
        hive.task.id,
        hive.link.cell.id,
    )
    assert await hive.store.nectar_content(nectar.id) == content
    assert run.saw.remembered.startswith("Sent to the Honey Store as a C1 finding")
