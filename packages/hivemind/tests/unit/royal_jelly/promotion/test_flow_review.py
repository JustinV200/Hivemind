"""Tests for the promotion flow's reviewer seam and its handling of an unreachable sandbox.

Fits into the Hive:
    Complements test_flow.py (codingrules section 14.2: split by feature). A tool proved on the
    real-Cell path is promoted at hive scope only after the Queen's review (ADR-0047, step 9.3a),
    and a runner that cannot reach its sandbox ends the run as a rejection, never as a crash.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.royal_jelly.promotion.flow for the module under test.
    - test_flow.py for the rest of the flow's behaviour.
"""

from __future__ import annotations

import pytest
from builders.royal_jelly import (
    FakeSandboxRunner,
    RecordingAnnouncer,
    RecordingHoney,
    make_brief,
    make_scaffold_result,
)

from hivemind.common.errors import HiveMindError
from hivemind.pheromone.trail import MemoryPheromoneTrail
from hivemind.royal_jelly.promotion import (
    PromotionFlow,
    PromotionOutcome,
    PromotionPorts,
    ToolIdentity,
)
from hivemind.royal_jelly.promotion.flow import REVIEW_DECLINED_CODE, SANDBOX_UNAVAILABLE_CODE
from hivemind.royal_jelly.registry import InMemoryCombRegistry, QueenPrincipal, WardenPrincipal
from hivemind.royal_jelly.scaffold import FakeScaffolder
from hivemind.royal_jelly.spec import QuarantineReport, ToolSpec
from hivemind.royal_jelly.tool_state import ToolState
from waggle.clock import FakeClock
from waggle.ids import new_hive_id, new_node_id
from waggle.messages.tool.authoring import QuarantinePath, ToolScope


class _Reviewer:
    """A `Reviewer` that answers a fixed verdict, or raises, and remembers what it was asked."""

    def __init__(self, verdict: bool | Exception) -> None:
        self._verdict = verdict
        self.asked: list[tuple[ToolSpec, QuarantineReport]] = []

    async def review(self, spec: ToolSpec, report: QuarantineReport) -> bool:
        self.asked.append((spec, report))
        if isinstance(self._verdict, Exception):
            raise self._verdict
        return self._verdict


async def _run(
    verdict: str | Exception = "pass",
    *,
    reviewer: _Reviewer | None = None,
    path: QuarantinePath = QuarantinePath.REAL_CELL,
    scope: ToolScope = ToolScope.HIVE,
    reviewed: bool = False,
) -> tuple[PromotionOutcome, InMemoryCombRegistry]:
    clock = FakeClock()
    brief = make_brief(clock, scope=scope)
    registry = InMemoryCombRegistry(clock)
    ports = PromotionPorts(
        FakeScaffolder([make_scaffold_result(brief)]),
        FakeSandboxRunner(verdict),
        registry,
        RecordingHoney(),
        RecordingAnnouncer(),
        lambda _platform: path,
        reviewer,
    )
    identity = ToolIdentity(new_hive_id(clock), new_node_id(clock), "system")
    flow = PromotionFlow(ports, MemoryPheromoneTrail(clock), identity, clock, max_retries=0)
    principal = (
        QueenPrincipal()
        if scope is ToolScope.HIVE
        else WardenPrincipal(brief.requested_by, brief.cell_id)  # type: ignore[arg-type]
    )
    return await flow.run(brief, principal, reviewed=reviewed), registry


async def test_a_real_cell_proof_at_hive_scope_is_promoted_reviewed_once_the_reviewer_accepts() -> (
    None
):
    reviewer = _Reviewer(True)

    outcome, registry = await _run(reviewer=reviewer)

    assert outcome.state is ToolState.PROMOTED
    (row,) = await registry.list_tools()
    assert row.reviewed is True and row.quarantine_path is QuarantinePath.REAL_CELL
    assert len(reviewer.asked) == 1 and reviewer.asked[0][1].path is QuarantinePath.REAL_CELL


async def test_a_real_cell_proof_the_reviewer_declines_is_rejected_and_not_promoted() -> None:
    outcome, registry = await _run(reviewer=_Reviewer(False))

    assert outcome.state is ToolState.REJECTED
    assert outcome.reason == f"promotion: {REVIEW_DECLINED_CODE}"
    assert await registry.list_tools() == ()


async def test_a_reviewer_that_cannot_answer_declines() -> None:
    outcome, registry = await _run(reviewer=_Reviewer(HiveMindError("the model is down")))

    assert outcome.state is ToolState.REJECTED and await registry.list_tools() == ()


@pytest.mark.parametrize(
    ("path", "scope"),
    [
        (QuarantinePath.SANDBOX_CELL, ToolScope.HIVE),  # Fully proved in the sandbox.
        (QuarantinePath.REAL_CELL, ToolScope.CELL),  # A Warden's own Cell tool, not the Queen's.
    ],
)
async def test_the_reviewer_is_asked_only_for_a_real_cell_proof_at_hive_scope(
    path: QuarantinePath, scope: ToolScope
) -> None:
    reviewer = _Reviewer(False)

    outcome, _registry = await _run(reviewer=reviewer, path=path, scope=scope)

    assert reviewer.asked == [] and outcome.state is ToolState.PROMOTED


async def test_a_promotion_the_caller_already_marked_reviewed_is_not_reviewed_again() -> None:
    reviewer = _Reviewer(False)

    outcome, _registry = await _run(reviewer=reviewer, reviewed=True)

    assert reviewer.asked == [] and outcome.state is ToolState.PROMOTED


@pytest.mark.parametrize(
    "failure", [HiveMindError("no sandbox"), OSError("refused"), TimeoutError()]
)
async def test_a_runner_that_cannot_reach_its_sandbox_ends_the_run_as_a_rejection(
    failure: Exception,
) -> None:
    outcome, registry = await _run(failure, path=QuarantinePath.SANDBOX_CELL)

    assert outcome.state is ToolState.REJECTED
    assert outcome.reason == f"quarantine: {SANDBOX_UNAVAILABLE_CODE}"
    assert await registry.list_tools() == ()
