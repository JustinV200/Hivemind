"""End-to-end: roadmap phase 4's exit criteria -- budget/handoff, clustering and Cell Wax bullets.

`.claude/roadmap.md` phase 4 exit criteria, one test per bullet (the forage/seats/throttle bullets
live in `test_phase4_exit_criteria_forage.py`, split out once this module reached the test-file
LOC cap): the flood test holds the budget; the handoff eval passes; the phase 3 scenarios still
pass at a quarter of the default hot-state budget; killing the fake provider mid-run clusters and
`hive wake` resumes with no duplicated work; a Warden's own CAUTION is written by autopilot and
appears in the planner's prompt only when its Cell is a candidate; a Drone's BLOCK reaches the
Queen's awake mode; an expired note leaves hot state on the next sweep.

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - .claude/roadmap.md phase 4 exit criteria, verbatim, for every bullet this module proves.
    - tests.e2e.test_flood for the ten-thousand-event flood test bullet 1 scales down from.
    - tests.evals.handoff.scenario/.grader for the handoff eval bullet 1 runs directly.
    - tests.e2e.test_kernel_on_hive_stand for the eight phase 3 scenarios, now all parametrised
      over `quarter_budget` there (this dispatch's own edit); bullet 1's own test here is a thin
      reference, not a duplicate of that matrix.
    - tests.e2e.test_clustering for the clustering scenario shape bullet 2 reuses.
    - tests.e2e.kernel_helpers for ClusterPair/build_cluster_pair, this module's own clustering
      wiring.
    - hivemind.queen.ticks.wax for handle_wax_proposed, bullet 6's own dispatch point.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from pathlib import Path

import pytest
from builders.cells import make_cell
from builders.cli import fake_manifest
from builders.llm import make_bound
from builders.memory import make_handoff, make_trigger_event
from builders.queen import make_queen_deps
from builders.supervision import make_alarm
from builders.workers import RunScript, ScriptedWorker, make_outcome
from e2e.kernel_helpers import (
    ClusterPair,
    HaikuScript,
    build_cluster_pair,
    checkpoint_recorded,
    default_worker_turn,
    find_task_by_title,
    set_budget_fraction,
    task_status_is,
    wait_until,
)
from evals.handoff.grader import build_report
from evals.handoff.scenario import run_fake_handoff_scenario

from hivemind.brood_chamber import TaskStatus
from hivemind.cell import CellKind, HoneyClearance, RealCellLease
from hivemind.cli.compose import Hive, build_hive, run_goal, run_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import DirectCallGate, FakeLLMProvider, ProviderCapabilities
from hivemind.llm.fake import text_response
from hivemind.llm.models import LLMRequest, LLMResponse
from hivemind.manifest import load_manifest
from hivemind.manifest.schema.supervision import DEFAULT_BUDGET_FRACTION
from hivemind.memory import (
    AssembleRequest,
    BeeBread,
    EstimateCounter,
    Handoff,
    MemoryContext,
    Principal,
    Scorable,
    TokenBudget,
    assemble,
    deposit_dropped_items,
)
from hivemind.memory.cell_wax import (
    MAX_WAX_TEXT_CHARS,
    WaxProposalInput,
    WaxState,
    propose_wax,
    write_wax,
)
from hivemind.memory.cell_wax import WaxSeverity as DomainWaxSeverity
from hivemind.memory.hot_state.summaries import AlarmSummary
from hivemind.pheromone.trail import TrailQuery
from hivemind.queen.awake import QueenSources
from hivemind.queen.cluster.orders import ClusterOrder, OrderKind, new_order_id
from hivemind.queen.cluster.tick import run_cluster_tick
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.queen import Queen
from hivemind.queen.state import ClusterState
from hivemind.queen.ticks.wax import handle_wax_proposed
from hivemind.wardens.warden import Warden
from hivemind.workers.base import WorkerOutcome
from hivemind.workers.context import WorkerContext
from hivemind.workers.roles.house_bee.sweep import SweepDeps, SweepOutcome, SweepWindow, run_sweep
from waggle.clock import FakeClock, SystemClock
from waggle.ids import TaskId, new_worker_id
from waggle.messages.cell import CellWaxProposed
from waggle.messages.cell.wax import WaxDecision, WaxOrigin
from waggle.messages.cell.wax import WaxSeverity as WireWaxSeverity
from waggle.messages.labels import HoneyClearance as WireHoneyClearance
from waggle.messages.task import TaskAssign

pytestmark = pytest.mark.e2e

# ──────────────────────────────────────────────────────────────────────────────
# 1. Budget and handoff
# ──────────────────────────────────────────────────────────────────────────────

_FLOOD_EVENT_COUNT = 1_000  # A tenth of test_flood.py's own 10,000: the same invariant, cheaper.
_FLOOD_BUDGET = TokenBudget(max_input_tokens=2_000, output_reserve=200, item_cap_chars=4_000)


async def test_the_flood_test_holds_the_budget() -> None:
    """A thin, 1,000-event run of test_flood.py's own invariant: the budget holds, drops archive.

    `test_flood.py` proves the same shape at the roadmap's own 10,000-event scale; duplicating
    that volume here would only cost time, not prove anything new (this module's own docstring).
    """
    deps, _link, _warden_end = make_queen_deps()
    human_inbox = HumanInbox()
    for i in range(_FLOOD_EVENT_COUNT):
        human_inbox.add_alarm(
            make_alarm(clock=deps.clock, detail=f"flood alarm {i}", raised_at=deps.clock.now())
        )

    sources = QueenSources(deps.chamber, deps.memory, human_inbox)
    principal = Principal(
        id=deps.identity.hive_id, slot=ModelSlot.QUEEN, clearance=HoneyClearance.C2, role="queen"
    )
    request = AssembleRequest(principal=principal, event=make_trigger_event(), budget=_FLOOD_BUDGET)
    dropped: list[Scorable] = []

    prompt = await assemble(request, sources, EstimateCounter(), on_drop=dropped.append)

    target_tokens = _FLOOD_BUDGET.max_input_tokens - _FLOOD_BUDGET.output_reserve
    event_tokens = await EstimateCounter().count(prompt.event_text)
    assert prompt.token_count <= target_tokens + event_tokens  # The budget held by construction.
    assert len(dropped) >= _FLOOD_EVENT_COUNT * 0.9  # Almost everything had to be dropped.
    assert all(isinstance(item, AlarmSummary) for item in dropped)

    # Every dropped item is findable in Bee Bread by id (roadmap 4.4's own second half).
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    entries = await deposit_dropped_items(dropped, ctx)
    assert len(entries) == len(dropped)


async def test_the_handoff_eval_passes() -> None:
    """The fake handoff scenario's report grades completion, no-redo and Handoff shape, all pass."""
    result = await run_fake_handoff_scenario()

    report = build_report(
        scenario="phase4-exit-criteria",
        expected_files=result.expected_files,
        present_files=result.present_files,
        handoff=result.handoff,
        second_bee_calls=result.second_bee_writes,
    )

    assert report.completion.passed, report.completion
    assert report.no_redo.passed, report.no_redo
    assert report.handoff_shape.passed, report.handoff_shape
    assert report.passed


def test_phase_3_scenarios_pass_at_a_quarter_budget(tmp_path: Path) -> None:
    """Thin reference: one phase 3 scenario, run directly at a quarter of the default budget.

    The exhaustive proof -- all eight phase 3 scenarios, at both capability levels, at both the
    default and a quarter budget -- is `test_kernel_on_hive_stand.py`'s own `_BUDGETS`
    parametrisation (this dispatch's own edit extended it from two scenarios to all eight; see
    that module's docstring). This test exercises the same manifest knob directly, so this
    module's own `-m e2e` run always touches roadmap 4.4's exit bar even if collection is
    filtered to just this file. A plain (non-async) test, like every scenario in that module:
    `build_hive`'s own default `stores=None` opens real SQLite via its own internal `asyncio.run`,
    which must run outside any event loop.
    """
    manifest_path = fake_manifest(tmp_path, capabilities="full")
    set_budget_fraction(manifest_path, DEFAULT_BUDGET_FRACTION / 4)
    manifest = load_manifest(manifest_path, {})
    script = HaikuScript(default_worker_turn)
    hive = build_hive(
        manifest, environ={}, clock=SystemClock(), responders={"fake": script.responder}
    )

    asyncio.run(_run_quarter_budget_goal(hive))


async def _run_quarter_budget_goal(hive: Hive) -> None:
    """The async body `test_phase_3_scenarios_pass_at_a_quarter_budget` drives."""
    async with run_hive(hive):
        report = await run_goal(
            hive, "write three haiku about bees", clearance=HoneyClearance.C1, timeout_s=10.0
        )
    assert report.succeeded, report


# ──────────────────────────────────────────────────────────────────────────────
# 2. Clustering: kill the fake provider mid-run, cluster, wake, resume with no duplicated work
# ──────────────────────────────────────────────────────────────────────────────

_FIRST_OBJECTIVE = "Write the FIRST haiku."
_SECOND_OBJECTIVE = "Write the SECOND haiku."  # The task clustering pauses mid-attempt.


def _cluster_plan() -> dict[str, object]:
    """Two independent tasks, so one finishes (first) while the other is still in flight."""

    def _task(key: str, objective: str) -> dict[str, object]:
        postcondition: dict[str, object] = {
            "kind": "FILE_EXISTS",
            "subject": f"scratch/{key}.txt",
            "argv": [],
            "expected": None,
        }
        return {
            "key": key,
            "title": objective,
            "objective": objective,
            "acceptance": [postcondition],
            "needs": {},
            "clearance": "C1",
            "depends_on": [],
        }

    return {"tasks": [_task("first", _FIRST_OBJECTIVE), _task("second", _SECOND_OBJECTIVE)]}


def _cluster_script(
    write_counts: dict[str, int], fresh_starts: list[int], resumed_with_ref: list[bool]
) -> RunScript:
    """One RunScript both tasks share, telling itself apart by `assignment.objective`.

    Args:
        write_counts: `{path_key: count}`, incremented once per `put_file` this script issues --
            the literal "count the fake session's writes per path" the exit bar asks for.
        fresh_starts: Appended to once per *fresh* (never resumed) start of the SECOND task's
            role -- the "no duplicated work" proof.
        resumed_with_ref: Appended to with `assignment.resume_from is not None` on the resumed
            attempt -- proves the resumed wire `TaskAssign` itself carried a `resume_from`.
    """

    async def script(
        ctx: WorkerContext, assignment: TaskAssign, resume_from: Handoff | None
    ) -> WorkerOutcome:
        key = "first" if assignment.objective == _FIRST_OBJECTIVE else "second"
        target = Path(f"scratch/{key}.txt")
        if resume_from is not None:
            resumed_with_ref.append(assignment.resume_from is not None)
            write_counts[key] = write_counts.get(key, 0) + 1
            await ctx.session.put_file(target, b"resumed")
            return make_outcome(summary="resumed: nothing left to redo")
        if assignment.objective == _SECOND_OBJECTIVE:
            fresh_starts.append(1)
            while not ctx.telemetry.handoff_requested:  # noqa: ASYNC110 -- cooperative poll.
                await asyncio.sleep(0)
            return make_outcome(claimed=False, handoff=make_handoff())
        write_counts[key] = write_counts.get(key, 0) + 1
        await ctx.session.put_file(target, b"done")
        return make_outcome(summary="first task done")

    return script


async def _wait_rounds(condition: Callable[[], Awaitable[bool] | bool], limit: int = 500) -> None:
    """Yield the loop until `condition()` holds, or fail after `limit` rounds (no real sleeps)."""
    for _ in range(limit):
        result = condition()
        if isinstance(result, Awaitable):
            result = await result
        if result:
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never became true within the round budget")


async def _run_first_task_and_capture_lease(
    pair: ClusterPair, queen: Queen, warden: Warden
) -> tuple[TaskId, TaskId, RealCellLease]:
    """Submit the goal, wait for the FIRST task, and return (goal_id, second_id, lease)."""
    deps = pair.deps
    goal_id = await queen.submit_goal("Write two haiku.", clearance=HoneyClearance.C1)
    await wait_until(lambda: task_status_is(deps, goal_id, TaskStatus.SUCCEEDED))
    second_id = await find_task_by_title(deps, goal_id, _SECOND_OBJECTIVE)
    lease = warden.lease
    assert lease is not None
    return goal_id, second_id, lease


async def _cluster_and_verify_paused(
    pair: ClusterPair,
    cluster_state: ClusterState,
    queen: Queen,
    second_id: TaskId,
    fresh_starts: list[int],
) -> None:
    """Kill the provider, cluster it, and verify checkpoint -> PAUSED trail order."""
    deps = pair.deps
    pair.provider.set_outage(True)
    await deps.orders.put_order(
        ClusterOrder(
            id=new_order_id(deps.clock),
            kind=OrderKind.CLUSTER,
            provider="fake",
            requested_at=deps.clock.now(),
        )
    )
    await run_cluster_tick(deps, cluster_state, queen.wardens)
    await _wait_rounds(lambda: len(fresh_starts) == 1)
    await _wait_rounds(lambda: task_status_is(deps, second_id, TaskStatus.PAUSED))
    await _wait_rounds(lambda: checkpoint_recorded(deps))
    kinds = [event.kind for event in await deps.trail.query(TrailQuery())]
    assert kinds.index("memory.checkpoint") < kinds.index("task.paused"), kinds


async def _wake_and_verify_resumed(
    pair: ClusterPair, cluster_state: ClusterState, queen: Queen, second_id: TaskId
) -> None:
    """`hive wake` after restoring the provider: the WAKE order resumes the paused task."""
    deps = pair.deps
    pair.provider.set_outage(False)
    await deps.orders.put_order(
        ClusterOrder(
            id=new_order_id(deps.clock),
            kind=OrderKind.WAKE,
            provider="fake",
            requested_at=deps.clock.now(),
        )
    )
    await run_cluster_tick(deps, cluster_state, queen.wardens)
    await _wait_rounds(lambda: task_status_is(deps, second_id, TaskStatus.SUCCEEDED))


async def test_killing_the_provider_mid_run_clusters_and_hive_wake_resumes_with_no_duplicated_work() -> (  # noqa: E501
    None
):
    """Every literal bullet: checkpoint->PAUSED order, an open lease, a WAKE resume, no dup work."""
    clock = SystemClock()
    write_counts: dict[str, int] = {}
    fresh_starts: list[int] = []
    resumed_with_ref: list[bool] = []
    script = _cluster_script(write_counts, fresh_starts, resumed_with_ref)
    pair = await build_cluster_pair(
        clock, _cluster_plan(), lambda role: ScriptedWorker(script, role=role)
    )
    queen, warden = pair.queen, pair.warden
    await warden.start()
    queen_task = asyncio.ensure_future(queen.run())
    warden_task = asyncio.ensure_future(warden.run())

    _goal_id, second_id, lease_before = await _run_first_task_and_capture_lease(pair, queen, warden)
    cluster_state = ClusterState()
    await _cluster_and_verify_paused(pair, cluster_state, queen, second_id, fresh_starts)
    assert warden.lease is lease_before  # Same lease object: never released or re-leased.

    await _wake_and_verify_resumed(pair, cluster_state, queen, second_id)

    assert resumed_with_ref == [True]  # The resumed TaskAssign carried a resume_from.
    assert fresh_starts == [1]  # Started fresh exactly once: never restarted from scratch.
    assert write_counts == {"first": 1, "second": 1}  # No path's tool effect ever repeated.

    await queen.stop()
    await asyncio.wait_for(queen_task, timeout=5.0)
    await warden.stop()
    await asyncio.wait_for(warden_task, timeout=5.0)


# ──────────────────────────────────────────────────────────────────────────────
# 6. Cell Wax: a Warden's own CAUTION, a Drone's BLOCK, and an expired note
# ──────────────────────────────────────────────────────────────────────────────

_WAX_TEXT = "This Cell's disk fills up under heavy load."


def _wax_proposal(
    cell_id: str, severity: WireWaxSeverity, proposer: str | None, origin: WaxOrigin
) -> CellWaxProposed:
    """Build one CellWaxProposed wire message, `_WAX_TEXT` as its own caution text."""
    return CellWaxProposed(
        cell_id=cell_id,
        severity=severity,
        text=_WAX_TEXT,
        reason="Saw two ENOSPC failures in a row.",
        clearance=WireHoneyClearance.C1,
        expires_at=None,
        origin=origin,
        proposer=proposer,
        task_id=None,
    )


def _wax_responder(action: str) -> Callable[[LLMRequest], LLMResponse]:
    """Build a Responder that answers a QueenDecision with `action`, on either output rung."""
    decision: Mapping[str, object] = {
        "action": action,
        "task_id": None,
        "reason": "Judged.",
        "binding": None,
    }

    def respond(request: LLMRequest) -> LLMResponse:
        if request.response_schema is not None:
            return text_response(json.dumps(decision))
        return text_response(f"```json\n{json.dumps(decision)}\n```")

    return respond


async def test_a_wardens_caution_about_its_own_cell_is_written_by_autopilot_and_appears_only_when_that_cell_is_a_candidate() -> (  # noqa: E501
    None
):
    """AUTOPILOT, no awake episode, and the caution text is cell-scoped in the assembled prompt."""
    provider = FakeLLMProvider(capabilities=ProviderCapabilities.full())
    deps, link, _warden_end = make_queen_deps(fake_provider=provider)
    other_cell = make_cell(kind=CellKind.REAL, clock=deps.clock)
    proposed = _wax_proposal(link.cell.id, WireWaxSeverity.CAUTION, link.warden_id, WaxOrigin.BEE)

    await handle_wax_proposed(deps, {link.warden_id: link}, link.warden_id, proposed)

    assert provider.calls == []  # AUTOPILOT: the Queen's own model was never called.
    written = [e for e in await deps.trail.query(TrailQuery()) if e.kind == "memory.wax_written"]
    assert len(written) == 1
    assert written[0].payload.get("decided_by") == "AUTOPILOT"

    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    principal = Principal(
        id=deps.identity.hive_id, slot=ModelSlot.QUEEN, clearance=HoneyClearance.C2, role="queen"
    )
    budget = TokenBudget(max_input_tokens=4_000, output_reserve=200, item_cap_chars=4_000)

    in_play = AssembleRequest(
        principal=principal,
        event=make_trigger_event(),
        budget=budget,
        cells_in_play=frozenset({link.cell.id}),
    )
    candidate_prompt = await assemble(in_play, sources, EstimateCounter())
    assert _WAX_TEXT in "".join(candidate_prompt.sections.values())

    not_in_play = AssembleRequest(
        principal=principal,
        event=make_trigger_event(),
        budget=budget,
        cells_in_play=frozenset({other_cell.id}),
    )
    other_prompt = await assemble(not_in_play, sources, EstimateCounter())
    assert _WAX_TEXT not in "".join(other_prompt.sections.values())


async def test_a_block_proposed_by_a_drone_reaches_the_queens_awake_mode() -> None:
    """A Drone's BLOCK is never autopiloted: an awake episode runs and resolves it."""
    provider = FakeLLMProvider(
        capabilities=ProviderCapabilities.full(), responder=_wax_responder("WRITE_WAX")
    )
    deps, link, _warden_end = make_queen_deps(fake_provider=provider)
    drone_id = new_worker_id(deps.clock)
    proposed = _wax_proposal(link.cell.id, WireWaxSeverity.BLOCK, drone_id, WaxOrigin.BEE)

    await handle_wax_proposed(deps, {link.warden_id: link}, link.warden_id, proposed)

    assert len(provider.calls) >= 1  # The Queen's own model was consulted: an awake episode ran.
    written = await deps.memory.list_wax(
        link.cell.id, frozenset({WaxState.WRITTEN}), HoneyClearance.C1
    )
    assert len(written) == 1
    assert written[0].decided_by is not None
    assert written[0].decided_by.value == "AWAKE"


async def test_an_expired_note_leaves_hot_state_on_the_next_sweep() -> None:
    """An expired WRITTEN note is swept to EXPIRED and drops out of the assembled prompt."""
    clock = FakeClock()
    deps, link, _warden_end = make_queen_deps(clock=clock)
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=clock)
    proposal = WaxProposalInput(
        cell_id=link.cell.id,
        severity=DomainWaxSeverity.CAUTION,
        text=_WAX_TEXT,
        reason="Expires soon.",
        clearance=HoneyClearance.C1,
        origin=WaxOrigin.BEE,
        proposer=str(link.warden_id),
        expires_at=clock.now() + timedelta(seconds=60),
    )
    proposed = await propose_wax(proposal, MAX_WAX_TEXT_CHARS, ctx)
    await write_wax(proposed, WaxDecision.AUTOPILOT, "test setup", ctx)

    clock.advance(120.0)  # Past expires_at; no sweep has run yet.
    sweep_deps = SweepDeps(
        memory=ctx, bee_bread=BeeBread(deps.memory), bound=make_bound(), gate=DirectCallGate()
    )
    window = SweepWindow(
        now=clock.now(), hot_window=timedelta(hours=1), allowance=HoneyClearance.C2
    )

    outcome: SweepOutcome = await run_sweep(sweep_deps, window)

    assert outcome.expired_wax == 1
    kinds = [e.kind for e in await deps.trail.query(TrailQuery())]
    assert "memory.wax_expired" in kinds

    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    principal = Principal(
        id=deps.identity.hive_id, slot=ModelSlot.QUEEN, clearance=HoneyClearance.C2, role="queen"
    )
    budget = TokenBudget(max_input_tokens=4_000, output_reserve=200, item_cap_chars=4_000)
    request = AssembleRequest(
        principal=principal,
        event=make_trigger_event(),
        budget=budget,
        cells_in_play=frozenset({link.cell.id}),
    )
    prompt = await assemble(request, sources, EstimateCounter())
    assert _WAX_TEXT not in "".join(prompt.sections.values())
