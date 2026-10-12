"""Tests for hivemind.royal_jelly.promotion.flow: request to promotion, the retry and every refusal.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/promotion/flow.py (codingrules section 3). Collaborators are
    fakes: a `FakeScaffolder`, a `FakeSandboxRunner`, the in-memory Comb Registry, recording Honey
    and announcer ports and an in-memory trail.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.promotion.flow for the module under test.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from builders.royal_jelly import (
    FakeSandboxRunner,
    RecordingAnnouncer,
    RecordingHoney,
    make_brief,
    make_platform,
    make_scaffold_result,
)

from hivemind.common.errors import HiveMindError
from hivemind.pheromone import TrailQuery
from hivemind.pheromone.trail import MemoryPheromoneTrail
from hivemind.royal_jelly.errors import PackageError, RoyalJellyError, SpecRejectedError
from hivemind.royal_jelly.promotion import (
    PromotionFlow,
    PromotionOutcome,
    PromotionPorts,
    ToolIdentity,
    sandbox_cell_path,
)
from hivemind.royal_jelly.registry import (
    InMemoryCombRegistry,
    QueenPrincipal,
    WardenPrincipal,
)
from hivemind.royal_jelly.scaffold import (
    FakeScaffolder,
    ScaffoldFailedError,
    ScaffoldResult,
    ToolRequestBrief,
)
from hivemind.royal_jelly.tool_state import ToolState
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id, new_warden_id
from waggle.messages.labels import OsFamily
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

_REJECTED_NEEDS_REVIEW = "hivemind.royal_jelly.promotion_refused.needs_review"


@dataclass
class _Rig:
    """One flow and every fake it was built over, so a test can read what each saw."""

    flow: PromotionFlow
    scaffolder: FakeScaffolder
    runner: FakeSandboxRunner
    registry: InMemoryCombRegistry
    honey: RecordingHoney
    announcer: RecordingAnnouncer
    trail: MemoryPheromoneTrail
    brief: ToolRequestBrief


@dataclass
class _Setup:
    """The knobs a test may turn on `_rig`; the defaults are a Queen promoting at HIVE scope."""

    scaffolds: list[ScaffoldResult | Exception] | None = None
    registry: InMemoryCombRegistry | None = None
    max_retries: int = 1
    path: QuarantinePath = QuarantinePath.SANDBOX_CELL
    honey: RecordingHoney | None = None
    announcer: RecordingAnnouncer | None = None
    brief: ToolRequestBrief | None = None


def _rig(*verdicts: str | Exception, setup: _Setup | None = None) -> _Rig:
    setup = setup or _Setup()
    clock = FakeClock()
    brief = setup.brief or make_brief(clock)
    scaffolder = FakeScaffolder(
        setup.scaffolds
        or [make_scaffold_result(brief), make_scaffold_result(brief, version="1.0.1")]
    )
    runner = FakeSandboxRunner(*verdicts)
    registry = setup.registry or InMemoryCombRegistry(clock)
    honey = setup.honey or RecordingHoney()
    announcer = setup.announcer or RecordingAnnouncer()
    trail = MemoryPheromoneTrail(clock)
    path = setup.path
    ports = PromotionPorts(scaffolder, runner, registry, honey, announcer, lambda _platform: path)
    identity = ToolIdentity(new_hive_id(clock), new_node_id(clock), "system")
    flow = PromotionFlow(ports, trail, identity, clock, max_retries=setup.max_retries)
    return _Rig(flow, scaffolder, runner, registry, honey, announcer, trail, brief)


async def _kinds(rig: _Rig) -> list[str]:
    events = await rig.trail.query(TrailQuery(subject_id=rig.brief.tool_id))
    return [event.kind for event in events]


async def test_a_package_that_passes_first_time_is_promoted_and_announced() -> None:
    rig = _rig("pass")

    outcome = await rig.flow.run(rig.brief, QueenPrincipal())

    assert outcome.state is ToolState.PROMOTED
    assert outcome.attempts == 1
    assert outcome.change is not None
    assert outcome.change.row.version == "1.0.0"
    current = await rig.registry.get("echo_tool", ToolScope.HIVE, None)
    assert outcome.report is not None
    assert current.is_current and current.package_sha256 == outcome.report.package_sha256
    assert await _kinds(rig) == [
        "tool.requested",
        "tool.scaffolded",
        "tool.quarantine_requested",
        "tool.quarantined",
        "tool.promoted",
    ]
    assert len(rig.honey.deposits) == 1
    assert [change.row.name for change, _spec in rig.announcer.changes] == ["echo_tool"]


async def test_the_runner_is_sent_the_package_the_scaffold_made_and_the_chosen_path() -> None:
    rig = _rig("pass", setup=_Setup(path=QuarantinePath.REAL_CELL))

    outcome = await rig.flow.run(rig.brief, QueenPrincipal(), reviewed=True)

    ((spec, package, path),) = rig.runner.calls
    assert (spec, path) == (outcome.spec, QuarantinePath.REAL_CELL)
    assert outcome.report is not None and outcome.change is not None
    assert outcome.report.package_sha256 == outcome.change.row.package_sha256
    assert package  # the bytes, never a path


async def test_a_failing_report_buys_one_retry_that_carries_the_report() -> None:
    rig = _rig("tests", "pass")

    outcome = await rig.flow.run(rig.brief, QueenPrincipal())

    assert outcome.state is ToolState.PROMOTED
    assert outcome.attempts == 2
    first, second = rig.scaffolder.briefs
    assert first.previous_report is None
    assert second.previous_report is not None
    assert second.previous_report.failing_gate == "tests"
    assert second.previous_tool_source is not None
    assert outcome.change is not None
    assert outcome.change.row.version == "1.0.1"  # the retry's scaffold is what was promoted
    assert await _kinds(rig) == [
        "tool.requested",
        "tool.scaffolded",
        "tool.quarantine_requested",
        "tool.quarantined",
        "tool.scaffolded",
        "tool.quarantine_requested",
        "tool.quarantined",
        "tool.promoted",
    ]


async def test_a_second_failure_rejects_with_the_failing_gate() -> None:
    rig = _rig("tests", "schema")

    outcome = await rig.flow.run(rig.brief, QueenPrincipal())

    assert outcome.state is ToolState.REJECTED
    assert outcome.attempts == 2
    assert outcome.reason == "quarantine: schema"
    assert outcome.report is not None and outcome.report.failing_gate == "schema"
    assert outcome.change is None
    assert await rig.registry.list_tools() == ()
    assert (await _kinds(rig))[-1] == "tool.rejected"
    assert rig.honey.deposits == [] and rig.announcer.changes == []
    events = await rig.trail.query(TrailQuery(kind="tool.rejected"))
    assert events[0].payload["code"] == "schema"
    assert events[0].payload["stage"] == "quarantine"


async def test_max_retries_zero_rejects_at_the_first_failure() -> None:
    rig = _rig("fuzz", setup=_Setup(max_retries=0))

    outcome = await rig.flow.run(rig.brief, QueenPrincipal())

    assert (outcome.state, outcome.attempts) == (ToolState.REJECTED, 1)
    assert len(rig.scaffolder.briefs) == 1


async def test_a_real_cell_report_at_hive_scope_needs_the_queens_review() -> None:
    rig = _rig("pass", setup=_Setup(path=QuarantinePath.REAL_CELL))

    outcome = await rig.flow.run(rig.brief, QueenPrincipal())

    assert outcome.state is ToolState.REJECTED
    assert outcome.reason == f"promotion: {_REJECTED_NEEDS_REVIEW}"
    assert await rig.registry.list_tools() == ()
    assert outcome.spec is not None and outcome.report is not None
    assert rig.honey.deposits == []
    events = await rig.trail.query(TrailQuery(kind="tool.rejected"))
    assert events[0].payload["code"] == _REJECTED_NEEDS_REVIEW


async def test_a_reviewed_real_cell_report_is_promoted() -> None:
    rig = _rig("pass", setup=_Setup(path=QuarantinePath.REAL_CELL))

    outcome = await rig.flow.run(rig.brief, QueenPrincipal(), reviewed=True)

    assert outcome.state is ToolState.PROMOTED
    assert outcome.change is not None and outcome.change.row.reviewed is True


async def test_a_warden_cannot_promote_at_hive_scope() -> None:
    rig = _rig("pass")
    clock = FakeClock()
    warden = WardenPrincipal(new_warden_id(clock), new_cell_id(clock))

    outcome = await rig.flow.run(rig.brief, warden)

    assert outcome.state is ToolState.REJECTED
    assert "wrong_principal" in outcome.reason


async def test_a_warden_promotes_a_cell_scope_tool_for_its_own_cell() -> None:
    clock = FakeClock()
    brief = make_brief(clock, scope=ToolScope.CELL)
    rig = _rig("pass", setup=_Setup(brief=brief))
    assert brief.cell_id is not None

    outcome = await rig.flow.run(brief, WardenPrincipal(new_warden_id(clock), brief.cell_id))

    assert outcome.state is ToolState.PROMOTED
    row = await rig.registry.get("echo_tool", ToolScope.CELL, brief.cell_id)
    assert row.cell_id == brief.cell_id


@pytest.mark.parametrize(
    "failure",
    [
        SpecRejectedError("echo_tool", ("widened",)),
        PackageError("too big"),
        ScaffoldFailedError("echo_tool", "timed out"),
    ],
)
async def test_a_scaffold_that_fails_rejects_without_asking_the_sandbox(failure: Exception) -> None:
    clock = FakeClock()
    brief = make_brief(clock)
    rig = _rig("pass", setup=_Setup(scaffolds=[failure], brief=brief))

    outcome = await rig.flow.run(brief, QueenPrincipal())

    assert outcome.state is ToolState.REJECTED
    assert outcome.spec is None and outcome.attempts == 1
    assert rig.runner.calls == []
    assert await _kinds(rig) == ["tool.requested", "tool.rejected"]
    events = await rig.trail.query(TrailQuery(kind="tool.rejected"))
    assert events[0].payload["stage"] == "scaffold"


async def test_a_scaffold_that_fails_on_the_retry_rejects_and_keeps_the_first_report() -> None:
    clock = FakeClock()
    brief = make_brief(clock)
    scaffolds: list[ScaffoldResult | Exception] = [
        make_scaffold_result(brief),
        SpecRejectedError("echo_tool", ("widened",)),
    ]
    rig = _rig("tests", setup=_Setup(scaffolds=scaffolds, brief=brief))

    outcome = await rig.flow.run(brief, QueenPrincipal())

    assert outcome.state is ToolState.REJECTED
    assert outcome.attempts == 2
    assert outcome.report is not None and outcome.report.failing_gate == "tests"


async def test_a_sandbox_that_cannot_be_reached_rejects_the_request() -> None:
    rig = _rig(RoyalJellyError("no sandbox Cell could be provisioned"))

    outcome = await rig.flow.run(rig.brief, QueenPrincipal())

    assert outcome.state is ToolState.REJECTED
    assert outcome.reason.startswith("quarantine: hivemind.royal_jelly.error")
    assert outcome.report is None
    assert await _kinds(rig) == [
        "tool.requested",
        "tool.scaffolded",
        "tool.quarantine_requested",
        "tool.rejected",
    ]


async def test_a_failing_honey_deposit_or_announcement_never_undoes_the_promotion() -> None:
    failing = _Setup(
        honey=RecordingHoney(HiveMindError("honey is down")),
        announcer=RecordingAnnouncer(OSError("link lost")),
    )
    rig = _rig("pass", setup=failing)

    outcome: PromotionOutcome = await rig.flow.run(rig.brief, QueenPrincipal())

    assert outcome.state is ToolState.PROMOTED
    assert len(rig.honey.deposits) == 1 and len(rig.announcer.changes) == 1
    assert (await rig.registry.get("echo_tool", ToolScope.HIVE, None)).is_current


async def test_promoting_a_version_that_already_exists_is_a_refusal_not_a_crash() -> None:
    first = _rig("pass")
    await first.flow.run(first.brief, QueenPrincipal())
    second = _rig("pass", setup=_Setup(registry=first.registry))

    outcome = await second.flow.run(second.brief, QueenPrincipal())

    assert outcome.state is ToolState.REJECTED
    assert "version_exists" in outcome.reason


def test_the_default_path_decider_always_takes_the_sandbox_cell() -> None:
    assert sandbox_cell_path(make_platform(os=OsFamily.WINDOWS)) is QuarantinePath.SANDBOX_CELL
