"""Tests for hivemind.cli.compose: build_hive, run_hive and run_goal.

Fits into the Hive:
    Mirrors src/hivemind/cli/compose/ (codingrules section 3): build_hive/run_hive/run_goal all
    live in compose/hive.py, so their tests are grouped in this one module rather than split by
    submodule.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.compose for the module under test.
    - .claude/roadmap.md step 3.22 scenario (a) for the three-haiku goal this module's own
      `test_run_goal_completes_the_three_haiku_goal_in_the_required_trail_order` proves end to end.
"""

from __future__ import annotations

import asyncio
import json
from importlib.resources import files
from pathlib import Path

import pytest
from builders.cli import HIVE_STAND_CORES, fake_manifest, pump_until_done
from builders.forage import make_grant

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, MemoryTaskStore, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cell.leavings import InMemoryLeavingsStore
from hivemind.cli.compose import GoalReport, Hive, HiveStores, build_hive, run_goal, run_hive
from hivemind.cli.stores import open_ledger
from hivemind.forage import ForageCapacity, RoleFootprint, RoyalReserve
from hivemind.forage.grant_state import GrantState
from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    FakeLLMProvider,
    LLMRequest,
    LLMResponse,
    Responder,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    Usage,
)
from hivemind.manifest import HiveManifest, load_manifest
from hivemind.memory import InMemoryMemoryStore
from hivemind.pheromone import PheromoneTrail, TrailQuery
from hivemind.pheromone.trail.memory import MemoryPheromoneTrail
from hivemind.queen import ForageLedger, SqliteOrderStore
from hivemind.queen.chat import InMemoryChatLog
from hivemind.queen.intake import InMemoryGoalRequestStore
from hivemind.wardens import WardenState
from waggle.clock import FakeClock
from waggle.ids import GrantId
from waggle.messages.task import WorkerRole

_FAKE_MODEL_ID = "test-model"

# The exact required trail order (roadmap step 3.21's own report requirement, matching 3.22
# scenario (a)): decompose -> placed -> granted -> spawned -> the Capping QA cycle -> accepted,
# with leased/released bookending the whole run. See this module's own report for why "leased"
# and "released" sit at the ends rather than between "placed" and "granted" as the roadmap's
# prose lists them: they are Hive-lifecycle events (once at hive.warden.start()/.stop()), not
# per-task events, so they can never interleave with a specific task's own dispatch sequence.
_REQUIRED_TRAIL_ORDER = (
    "cell.leased",
    "queen.planned",
    "queen.assigned",
    "forage.granted",
    "worker.spawned",
    "capping.proposed",
    "capping.capped",
    "capping.applied",
    "capping.verified",
    "task.succeeded",
    "cell.released",
)

_THREE_HAIKU_PLAN = {
    "tasks": [
        {
            "key": "haikus",
            "title": "Write three haiku about bees",
            "objective": "Write three haiku about bees to separate files.",
            "acceptance": [
                {"kind": "FILE_EXISTS", "subject": f"haiku_{i}.txt", "argv": [], "expected": None}
                for i in (1, 2, 3)
            ],
            "needs": {},
            "clearance": "C1",
            "depends_on": [],
        }
    ]
}


def _text_response(text: str) -> LLMResponse:
    """Build a plain-text LLMResponse, mirroring hivemind.llm.fake.text_response's own shape."""
    return LLMResponse(
        parts=(TextPart(text=text),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=0, output_tokens=0),
        model=_FAKE_MODEL_ID,
    )


def _tool_call_response(*calls: ToolCall) -> LLMResponse:
    """Build a tool-call LLMResponse, mirroring hivemind.llm.fake.tool_call_response's own shape."""
    return LLMResponse(
        parts=tuple(ToolCallPart(call=call) for call in calls),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(input_tokens=0, output_tokens=0),
        model=_FAKE_MODEL_ID,
    )


def _three_haiku_responder() -> Responder:
    """Script the planner's one plan call and the Drone's two-round tool loop.

    Distinguishes a planning call from a Drone call by `request.slot` (`ModelSlot.QUEEN` vs
    `ModelSlot.WORKER`), not by prompt content: `fake_manifest` binds every slot to the same one
    `"fake"` provider, so this is the one responder every call in the Hive passes through. Also
    answers correctly at every degradation rung a request might be on (`hivemind.llm.ladders`'
    own NATIVE/JSON_MODE/PROMPTED and native-vs-prompted tool protocol), so the same responder
    serves both the full- and zero-capability scenarios below: a structured-output request with no
    `response_schema` is on the PROMPTED rung and expects one fenced ` ```json ` block
    (`hivemind.llm.ladders.structured._build_request_for_rung`'s own tell); a tool-loop request
    with no `tools` is on the prompted tool protocol and expects one fenced ` ```tool ` block per
    call (`hivemind.llm.ladders.tools._prompted_protocol`'s own tell).
    """
    rounds = {"worker": 0}
    write_calls = tuple(
        (f"call_{i}", "write_file", {"path": f"haiku_{i}.txt", "content": f"bees hum {i}"})
        for i in (1, 2, 3)
    )

    def responder(request: LLMRequest) -> LLMResponse:
        if request.slot is ModelSlot.QUEEN:
            return _plan_response(request)
        if request.slot is ModelSlot.WORKER:
            rounds["worker"] += 1
            if rounds["worker"] == 1:
                return _write_files_response(request, write_calls)
            return _text_response("Three haiku written.")
        return _text_response("{}")  # A Warden/Queen awake episode, never expected to fire here.

    return responder


def _blocked_responder() -> Responder:
    """Script a goal that can never finish: the plan, then a Drone that only ever asks the human.

    Nobody answers, so the task stays BLOCKED and the goal is never terminal: the deterministic
    ground `test_run_goal_times_out_cleanly` needs. A goal that could finish would race the poll
    loop, whose terminal check rightly comes before its deadline check.
    """
    ask = (("ask_1", "ask", {"text": "Which season should the haiku be about?"}),)

    def responder(request: LLMRequest) -> LLMResponse:
        if request.slot is ModelSlot.QUEEN:
            return _plan_response(request)
        if request.slot is ModelSlot.WORKER:
            return _write_files_response(request, ask)
        return _text_response("{}")

    return responder


def _plan_response(request: LLMRequest) -> LLMResponse:
    """Answer the planner's own call, on whichever structured-output rung `request` is on."""
    plan_json = json.dumps(_THREE_HAIKU_PLAN)
    if request.response_schema is not None:
        return _text_response(plan_json)  # NATIVE or JSON_MODE: the schema travelled with it.
    return _text_response(f"```json\n{plan_json}\n```")  # PROMPTED: one fenced block expected.


def _write_files_response(
    request: LLMRequest, calls: tuple[tuple[str, str, dict[str, str]], ...]
) -> LLMResponse:
    """Answer the Drone's first round with `calls`, on whichever tool protocol `request` is on."""
    if request.tools:
        # Native protocol: tools travelled in request.tools; reply with real ToolCallParts.
        return _tool_call_response(
            *(
                ToolCall(id=call_id, name=name, arguments=arguments)
                for call_id, name, arguments in calls
            )
        )
    # Prompted protocol: request.tools is always empty (module docstring); one fenced ```tool
    # block per call, each {"name": ..., "arguments": {...}} (extract_tool_blocks's own format).
    blocks = "\n\n".join(
        f"```tool\n{json.dumps({'name': name, 'arguments': arguments})}\n```"
        for _call_id, name, arguments in calls
    )
    return _text_response(blocks)


def _in_memory_stores(clock: FakeClock, manifest: HiveManifest) -> HiveStores:
    """Build an in-memory HiveStores sharing `clock`, so a test never touches real SQLite."""
    trail: PheromoneTrail = MemoryPheromoneTrail(clock)
    identity = ChamberIdentity(
        hive_id=manifest.hive.id, node_id=manifest.hive.node_id, actor="system"
    )
    chamber = BroodChamber(MemoryTaskStore(trail), clock, identity)
    return HiveStores(
        trail=trail,
        chamber=chamber,
        memory=InMemoryMemoryStore(trail),
        leavings=InMemoryLeavingsStore(trail),
        goal_requests=InMemoryGoalRequestStore(trail),
        chat=InMemoryChatLog(trail),
    )


def _build_test_hive(
    tmp_path: Path, *, responder: Responder | None = None, capabilities: str = "full"
) -> tuple[Hive, FakeClock]:
    """Build a Hive over `fake_manifest` and in-memory stores, sharing one FakeClock throughout."""
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, capabilities=capabilities, clock=clock), {})
    stores = _in_memory_stores(clock, manifest)
    responders = {"fake": responder} if responder is not None else None
    hive = build_hive(manifest, environ={}, clock=clock, stores=stores, responders=responders)
    return hive, clock


def test_build_hive_wires_the_registry_warden_and_queen(tmp_path: Path) -> None:
    hive, _clock = _build_test_hive(tmp_path)

    # isinstance, not a kind branch: this checks a capability of the test double the same way a
    # production caller checks isinstance(provider, LLMProvider) implementations elsewhere.
    provider = hive.registry.provider("fake")
    assert isinstance(provider, FakeLLMProvider)
    assert hive.warden.lease is None  # start() has not run yet.
    # Roadmap step 10.3: attaching is the Queen's awaited warden_spawn check, so run_hive does it.
    assert hive.queen.wardens == ()
    assert hive.warden_link.cell.source == "hive_stand"


def test_build_hive_builds_the_wardens_guard_policy_from_the_manifest(tmp_path: Path) -> None:
    # Roadmap step 10.2: [guard] policy_file resolves against the manifest's own directory, and
    # the table's own entries apply on top of that file.
    clock = FakeClock()
    path = fake_manifest(tmp_path, clock=clock)
    shipped = (files("hivemind.guard.defaults") / "policy.toml").read_text(encoding="utf-8")
    (tmp_path / "guard.toml").write_text(shipped.replace("deny = []", 'deny = ["geo:*"]', 1))
    with path.open("a", encoding="utf-8") as manifest_file:
        manifest_file.write('\n[guard]\npolicy_file = "guard.toml"\ndeny = ["wifi:scan"]\n')
    manifest = load_manifest(path, {})

    hive = build_hive(manifest, environ={}, clock=clock, stores=_in_memory_stores(clock, manifest))

    assert hive.warden._deps.guard.deny.as_strings() == ("geo:*", "wifi:scan")


def test_build_hive_carries_the_manifests_footprints_reserve_and_grant_ttl(tmp_path: Path) -> None:
    hive, _clock = _build_test_hive(tmp_path)

    # fake_manifest's own [forage.roles.drone]/[forage.reserve]/[forage] grant_ttl_s values
    # (docs/manifests's own shape); QueenDeps is private to the Queen, but a test reaching into it
    # is the only way to prove build_hive actually threaded the manifest through rather than
    # falling back to QueenDeps's own defaults (roadmap step 3.21's own report requirement).
    deps = hive.queen._deps
    assert deps.footprints[WorkerRole.DRONE] == RoleFootprint(
        cpu_cores=0.5,
        memory_bytes=268_435_456,
        seats=1,
        token_rate_per_minute=20_000.0,
        exoskeleton_extra_memory_bytes=0,
    )
    assert deps.reserve == RoyalReserve()
    assert deps.grant_ttl_s == 300.0
    # roadmap step 4.7: build_queen_deps constructs a ForageLedger sharing the manifest's own
    # reserve, rather than falling back to QueenDeps's own default-constructed one.
    assert isinstance(deps.ledger, ForageLedger)
    assert deps.ledger.reserve == deps.reserve
    # The zero-grant fix: [forage] zero_grant_patience_s, fake_manifest leaving the default.
    assert deps.dispatch.waits.patience_s == 300.0


def test_build_hive_gives_the_hive_stands_link_a_reader_of_its_capacity_as_it_stands(
    tmp_path: Path,
) -> None:
    # The zero-grant fix: every grant for the Hive Stand is sized from a fresh reading, so a load
    # that has dropped since build_hive probed the host is seen at the next dispatch pass.
    hive, _clock = _build_test_hive(tmp_path)
    reader = hive.warden_link.live_capacity
    assert reader is not None

    async def _read_once() -> ForageCapacity:
        """Take one reading, the way the dispatcher does before sizing a grant."""
        assert reader is not None  # Narrowed above; restated for the closure.
        return await reader()

    reading = asyncio.run(_read_once())

    # Static totals as probed (fake_manifest pins the cores), live figures re-read.
    assert reading.host.cores == HIVE_STAND_CORES
    assert reading.max_sub_bees == hive.warden_link.cell.capacity.max_sub_bees


def test_build_hive_wires_the_queen_deps_ledger_over_sqlite_and_restores_it(
    tmp_path: Path,
) -> None:
    # Roadmap step 4.8: build_queen_deps opens a SqliteLedgerStore on [hive] db (even when the
    # trail/chamber/memory stores themselves were overridden with in-memory fakes, since a
    # ForageLedger's own store is wired independently of HiveStores) and restores from it.
    clock = FakeClock()
    manifest = load_manifest(fake_manifest(tmp_path, clock=clock), {})
    stores = _in_memory_stores(clock, manifest)
    hive = build_hive(manifest, environ={}, clock=clock, stores=stores)
    grant = make_grant(clock=clock, state=GrantState.ACTIVE, max_sub_bees=3)
    asyncio.run(hive.queen._deps.ledger.record_grant(grant))

    db = manifest.resolve_path(manifest.hive.db)
    restored = ForageLedger(reserve=manifest.forage.reserve, store=open_ledger(db))
    asyncio.run(restored.restore())

    assert restored.grant(grant.id) == grant


def test_build_hive_wires_the_queen_deps_cluster_orders_over_sqlite(tmp_path: Path) -> None:
    """Roadmap step 4.9 (Clustering): build_queen_deps opens a SqliteOrderStore on [hive] db."""
    hive, _clock = _build_test_hive(tmp_path)

    assert isinstance(hive.queen._deps.orders, SqliteOrderStore)


def test_build_hive_wires_the_queen_deps_provider_lookup_to_the_registry(tmp_path: Path) -> None:
    """Roadmap step 4.9: provider_lookup resolves the same instance the registry does."""
    hive, _clock = _build_test_hive(tmp_path)

    deps = hive.queen._deps
    assert deps.provider_lookup is not None
    assert deps.provider_lookup("fake") is hive.registry.provider("fake")


@pytest.fixture
def plain_hive(tmp_path: Path) -> tuple[Hive, FakeClock]:
    """A Hive with no responder scripted, for tests that never submit a goal.

    A plain `def` fixture, not a fixture-factory closure: `build_hive` must run before
    pytest-asyncio's own event loop starts for the async test consuming this (module docstring's
    own "Key invariants" on `asyncio.run` nesting) -- calling it from inside an `async def` test
    body, even at the very first line, is already too late, since the whole coroutine is already
    running on that loop by then.
    """
    return _build_test_hive(tmp_path)


@pytest.fixture
def three_haiku_hive(tmp_path: Path) -> tuple[Hive, FakeClock]:
    """A Hive scripted for the three-haiku goal, at full provider capabilities."""
    return _build_test_hive(tmp_path, responder=_three_haiku_responder())


@pytest.fixture
def blocked_hive(tmp_path: Path) -> tuple[Hive, FakeClock]:
    """A Hive whose goal can never finish (`_blocked_responder`)."""
    return _build_test_hive(tmp_path, responder=_blocked_responder())


@pytest.fixture
def three_haiku_hive_zero_capabilities(tmp_path: Path) -> tuple[Hive, FakeClock]:
    """A Hive scripted for the three-haiku goal, at zero provider capabilities."""
    return _build_test_hive(tmp_path, responder=_three_haiku_responder(), capabilities="none")


async def test_run_hive_starts_and_stops_cleanly_and_leaves_scratch_empty(
    tmp_path: Path, plain_hive: tuple[Hive, FakeClock]
) -> None:
    hive, _clock = plain_hive

    async with run_hive(hive):
        state_while_running = hive.warden.state
        assert hive.warden.lease is not None
        assert state_while_running in (WardenState.ACTIVE, WardenState.WATCH)

    state_after_exit = hive.warden.state
    assert state_after_exit is WardenState.STOPPED
    assert (
        hive.warden.lease is not None
    )  # release() does not forget the lease; it marks it RELEASED.
    assert list((tmp_path / "scratch").iterdir()) == []  # Left as found.


async def test_run_hive_attaches_the_warden_through_the_guard_and_records_warden_spawned(
    plain_hive: tuple[Hive, FakeClock],
) -> None:
    hive, _clock = plain_hive

    async with run_hive(hive):
        assert hive.queen.wardens == (hive.warden_link,)

    spawned = await hive.stores.trail.query(TrailQuery(kind="warden.spawned"))
    assert [event.subject_id for event in spawned] == [hive.warden_link.warden_id]
    # The Hive Stand's Warden leased under its own `cell:hive_stand`, so nothing was refused.
    assert await hive.stores.trail.query(TrailQuery(kind="guard.denied")) == ()


async def test_run_hive_leaves_no_pending_tasks_after_it_exits(
    plain_hive: tuple[Hive, FakeClock],
) -> None:
    """This dispatch's own shutdown-hygiene proof: run_hive's own teardown reaps every task."""
    hive, _clock = plain_hive
    before = asyncio.all_tasks() - {asyncio.current_task()}

    async with run_hive(hive):
        pass

    after = asyncio.all_tasks() - {asyncio.current_task()}
    assert after == before


async def test_run_hive_leaves_no_pending_tasks_when_the_caller_raises(
    plain_hive: tuple[Hive, FakeClock],
) -> None:
    """Rule 4's own proof: a caller's exception must not let the TaskGroup cancel a live tick.

    Before this fix, `hive.queen.stop()` was fired-and-forgotten and neither `run()` task was
    awaited before this contextmanager's own `asyncio.TaskGroup` block ended: an exception raised
    here would reach `TaskGroup.__aexit__` while a tick might still be in flight, and the
    TaskGroup would cancel it itself instead of letting the cooperative `stop()` above finish.
    `asyncio.TaskGroup.__aexit__` always wraps a body-raised exception in an `ExceptionGroup`
    (its own documented contract, unrelated to this fix), so that -- not the bare `RuntimeError`
    -- is what a caller of `run_hive` actually sees.
    """
    hive, _clock = plain_hive
    before = asyncio.all_tasks() - {asyncio.current_task()}

    with pytest.raises(ExceptionGroup) as exc_info:
        async with run_hive(hive):
            raise RuntimeError("boom")
    assert isinstance(exc_info.value.exceptions[0], RuntimeError)

    after = asyncio.all_tasks() - {asyncio.current_task()}
    assert after == before


async def test_run_goal_completes_the_three_haiku_goal_in_the_required_trail_order(
    tmp_path: Path, three_haiku_hive: tuple[Hive, FakeClock]
) -> None:
    hive, clock = three_haiku_hive

    async def _scenario() -> GoalReport:
        async with run_hive(hive):
            return await run_goal(
                hive,
                "write three haiku about bees to separate files",
                clearance=HoneyClearance.C1,
                timeout_s=60.0,
            )

    report = await pump_until_done(clock, _scenario())

    assert report.succeeded is True
    assert report.timed_out is False
    assert len(report.tasks) == 1
    assert report.tasks[0].status is TaskStatus.SUCCEEDED

    events = await hive.stores.trail.query(TrailQuery())
    kinds = [event.kind for event in events]
    _assert_kinds_in_order(kinds, _REQUIRED_TRAIL_ORDER)
    assert list((tmp_path / "scratch").iterdir()) == []  # Left as found, even after real writes.


def _priced_three_haiku_responder() -> Responder:
    """Script the same plan/tool-loop shape as `_three_haiku_responder`, but with real cost_usd.

    Every response in this module's own shared helpers (`_text_response`, `_tool_call_response`)
    stamps `Usage(input_tokens=0, output_tokens=0)`, `cost_usd` defaulting to `None` -- a
    `FakeLLMProvider` never computes a price from `[forage.map.<source>].cost` itself (unlike a
    real provider adapter, pricing is this fake's own scripted `Usage`, verbatim), so `cost_usd`
    is stamped directly here rather than relying on the manifest.
    """
    rounds = {"worker": 0}
    write_calls = tuple(
        (f"call_{i}", "write_file", {"path": f"haiku_{i}.txt", "content": f"bees hum {i}"})
        for i in (1, 2, 3)
    )

    def responder(request: LLMRequest) -> LLMResponse:
        if request.slot is ModelSlot.QUEEN:
            return _plan_response(request)
        if request.slot is ModelSlot.WORKER:
            rounds["worker"] += 1
            base = (
                _write_files_response(request, write_calls)
                if rounds["worker"] == 1
                else _text_response("Three haiku written.")
            )
            # A real, priced Usage, so this lane's own llm.call carries a non-zero cost_usd for
            # the ledger to attribute (module docstring); every other field stays `base`'s own.
            usage = Usage(input_tokens=100, output_tokens=50, cost_usd=0.05)
            return base.model_copy(update={"usage": usage})
        return _text_response("{}")  # A Warden/Queen awake episode, never expected to fire here.

    return responder


@pytest.fixture
def priced_three_haiku_hive(tmp_path: Path) -> tuple[Hive, FakeClock]:
    """A three-haiku Hive whose Drone calls carry a real, priced `cost_usd`.

    A plain `def` fixture, not a fixture-factory closure, for the same reason `plain_hive` is
    (this module's own docstring on `plain_hive`): `build_hive` must run before pytest-asyncio's
    own event loop starts, since it makes its own `asyncio.run` call internally
    (`hivemind.cli.compose.deps.build_ledger`); calling it from inside an `async def` test body
    raises `RuntimeError: asyncio.run() cannot be called from a running event loop`.
    """
    return _build_test_hive(tmp_path, responder=_priced_three_haiku_responder())


async def test_a_sub_bees_llm_call_carries_its_grant_id_and_moves_the_ledgers_spend(
    priced_three_haiku_hive: tuple[Hive, FakeClock],
) -> None:
    """Roadmap step 4.8's own wiring step: per-grant lane attribution, proven end to end.

    `hivemind.wardens.spawn.spawn.spawn_sub_bee` builds each sub-bee's own `call_gate` from
    `WardenDeps.lane_for_grant`, so every `llm.call` it makes on that lane carries the grant id
    (`hivemind.llm.fanner.lane.FannerLane._call_payload`); `build_fanner`'s own `LedgerRecorder`
    (chained through `CompositeLlmEventRecorder`) then attributes that call's cost to the same
    grant in the ledger. A priced source is needed to prove spend actually moves, not just that
    the id rides along -- `fake_manifest`'s own default source is free (see `_price_the_fake_
    source`), so `priced_three_haiku_hive` patches one in rather than reusing `three_haiku_hive`.
    """
    hive, clock = priced_three_haiku_hive

    async def _scenario() -> GoalReport:
        async with run_hive(hive):
            return await run_goal(
                hive,
                "write three haiku about bees to separate files",
                clearance=HoneyClearance.C1,
                timeout_s=60.0,
            )

    report = await pump_until_done(clock, _scenario())
    assert report.succeeded is True

    events = await hive.stores.trail.query(TrailQuery(family="llm", kind="llm.call"))
    # A sub-bee's own call carries its grant id (hivemind.wardens.deps.WardenDeps.lane_for_grant);
    # the Queen's own planning call, on her unattributed call_gate, carries none -- both are
    # expected, so this asserts at least one attributed call happened, not that every call was.
    grant_ids = {event.payload.get("grant_id") for event in events if "grant_id" in event.payload}
    assert grant_ids

    (grant_id,) = grant_ids
    assert isinstance(grant_id, str)
    grant = hive.queen._deps.ledger.grant(GrantId(grant_id))
    assert grant is not None
    assert grant.spent > 0.0  # The ledger's own spend for that grant actually moved.


async def test_run_goal_times_out_cleanly(blocked_hive: tuple[Hive, FakeClock]) -> None:
    hive, clock = blocked_hive

    async def _scenario() -> GoalReport:
        async with run_hive(hive):
            # A timeout far shorter than a single poll interval, on a goal that can never finish
            # (its Drone only asks, and nobody answers): the first re-check always times out.
            return await run_goal(
                hive,
                "write three haiku about bees to separate files",
                clearance=HoneyClearance.C1,
                timeout_s=1e-6,
            )

    report = await pump_until_done(clock, _scenario())

    assert report.timed_out is True
    assert report.succeeded is False


async def test_run_goal_works_with_zero_capability_fake_provider(
    three_haiku_hive_zero_capabilities: tuple[Hive, FakeClock],
) -> None:
    """Finish the goal with a zero-capability fake provider.

    The prompted-rung ladders must still finish the goal against a `ProviderCapabilities.none()`
    provider (roadmap step 3.22's own "full and zero capabilities" requirement).
    """
    hive, clock = three_haiku_hive_zero_capabilities

    async def _scenario() -> GoalReport:
        async with run_hive(hive):
            return await run_goal(
                hive,
                "write three haiku about bees to separate files",
                clearance=HoneyClearance.C1,
                timeout_s=60.0,
            )

    report = await pump_until_done(clock, _scenario())

    assert report.succeeded is True
    assert report.timed_out is False


def _assert_kinds_in_order(kinds: list[str], required: tuple[str, ...]) -> None:
    """Assert every kind in `required` appears in `kinds`, in that relative order.

    Uses each kind's *first* occurrence: several `required` kinds (the Capping cycle) repeat once
    per file this scenario writes, and only their first appearance needs to respect the order.
    """
    first_index = []
    for kind in required:
        assert kind in kinds, f"{kind!r} never appeared on the trail: {kinds}"
        first_index.append(kinds.index(kind))
    assert first_index == sorted(first_index), (
        f"expected {required} in order, got first-occurrence indexes {first_index} in {kinds}"
    )
