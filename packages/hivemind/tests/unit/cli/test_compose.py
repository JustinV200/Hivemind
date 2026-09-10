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

import json
from pathlib import Path

import pytest
from builders.cli import fake_manifest, pump_until_done

from hivemind.brood_chamber import BroodChamber, ChamberIdentity, MemoryTaskStore, TaskStatus
from hivemind.cell import HoneyClearance
from hivemind.cli.compose import GoalReport, Hive, HiveStores, build_hive, run_goal, run_hive
from hivemind.forage import RoleFootprint, RoyalReserve
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
from hivemind.wardens import WardenState
from waggle.clock import FakeClock
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
    return HiveStores(trail=trail, chamber=chamber, memory=InMemoryMemoryStore(trail))


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
    assert hive.queen.wardens == (hive.warden_link,)
    assert hive.warden_link.cell.source == "hive_stand"


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


async def test_run_goal_times_out_cleanly(three_haiku_hive: tuple[Hive, FakeClock]) -> None:
    hive, clock = three_haiku_hive

    async def _scenario() -> GoalReport:
        async with run_hive(hive):
            # A timeout far shorter than a single poll interval: the very first re-check after
            # one clock advance already exceeds it, regardless of how far the goal got.
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
