"""Unit tests for hivemind.workers.tools.proposals: make_proposal, cap, describe, tool_output.

Roadmap step 10.3's own tests at the bottom: a Capping ALLOWLIST refusal that names a missing
capability is also the Guard's own `guard.denied`, at `session_outside_scratch` for a write leaving
scratch and at `tool_invocation` for anything else; a refusal no capability names records nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from builders.capping import FakeLeaseView, make_action, make_postcondition
from builders.workers import make_assignment, make_context, make_gui_context, run_tool

from hivemind.exoskeleton import Peripherals, ScreenSize
from hivemind.exoskeleton.antennae import FakeAntennae
from hivemind.exoskeleton.compound_eye import FakeCompoundEye, FakeScreen
from hivemind.guard import CapabilitySet
from hivemind.pheromone import TrailQuery
from hivemind.supervision.capping import (
    CappingGate,
    GateDeps,
    GateOutcome,
    JudgeOutcome,
    JudgeVerdict,
    LeaseView,
    ProposalState,
    RiskTier,
)
from hivemind.supervision.capping.leave import Asker
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.proposals import (
    MAX_REVIEW_CHARS,
    REVIEW_REJECTED_REASON,
    ProposalRequest,
    cap,
    describe,
    make_proposal,
    tool_output,
)
from hivemind.workers.tools.registry import ToolInvocation
from waggle.clock import FakeClock
from waggle.ids import MessageId
from waggle.messages import AlarmSeverity
from waggle.messages.capping import ActionKind
from waggle.messages.labels import PostconditionKind
from waggle.messages.supervision import AlarmKind

_SIZE = ScreenSize(100, 100)
_OUTSIDE = Path("/outside")  # A root the lease reaches beyond scratch, like a declared keep_root.


def _desk(
    gate: Callable[[GateDeps], CappingGate] = CappingGate, recording_id: str | None = None
) -> WorkerContext:
    """A display nothing ever changes on, driven through `gate`."""
    clock = FakeClock()
    peripherals = Peripherals(
        compound_eye=FakeCompoundEye(FakeScreen(_SIZE), clock), antennae=FakeAntennae(_SIZE)
    )
    return make_gui_context(peripherals, clock, gate=gate, recording_id=recording_id)


def _reviewed(verdict: JudgeVerdict) -> Callable[[GateDeps], CappingGate]:
    """Build a gate that hands back `verdict` as the judge's review, as the Warden's gate does."""

    class _ReviewedGate(CappingGate):
        async def run(
            self,
            proposal_id: MessageId,
            capabilities: CapabilitySet,
            lease: LeaseView,
            asker: Asker | None = None,
        ) -> GateOutcome:
            outcome = await super().run(proposal_id, capabilities, lease, asker)
            return outcome.model_copy(update={"review": verdict})

    return _ReviewedGate


def _verdict(outcome: JudgeOutcome, *reasons: str) -> JudgeVerdict:
    """A judge's verdict on the irreversible tier."""
    return JudgeVerdict(outcome=outcome, reasons=reasons, rubric_id="irreversible")


def test_make_proposal_stamps_every_id_from_ctx_and_assignment() -> None:
    ctx = make_context()
    assignment = make_assignment()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )

    proposal = make_proposal(ctx, assignment, request)

    assert proposal.task_id == assignment.task_id
    assert proposal.cell_id == ctx.cell.id
    assert proposal.proposer == ctx.worker_id
    assert proposal.risk_tier is RiskTier.SCRATCH_WRITE
    assert proposal.spend_estimate_usd == 0.0
    assert proposal.state is ProposalState.PROPOSED


async def test_cap_proposes_then_runs_and_returns_a_terminal_outcome() -> None:
    ctx = make_context()
    assignment = make_assignment()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )
    proposal = make_proposal(ctx, assignment, request)

    outcome = await cap(ToolInvocation(ctx=ctx, assignment=assignment), proposal)

    assert outcome.state in (
        ProposalState.VERIFIED,
        ProposalState.REJECTED,
        ProposalState.ROLLED_BACK,
    )
    assert outcome.proposal_id == proposal.id


async def test_describe_never_includes_the_action_diff_text() -> None:
    ctx = make_context()
    assignment = make_assignment()
    distinctive_diff_text = "@@ -0,0 +1,1 @@\n+top distinctive line\n"
    action = make_action(diff=distinctive_diff_text, paths=("note.txt",))
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=action,
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )
    proposal = make_proposal(ctx, assignment, request)

    outcome = await cap(ToolInvocation(ctx=ctx, assignment=assignment), proposal)
    text = describe(outcome)

    assert "top distinctive line" not in text
    assert "state=" in text and "reason=" in text


async def test_cap_alarms_at_once_when_a_gui_action_is_rolled_back() -> None:
    ctx = _desk()

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "expect": {"region": "0,0,10,10"}})

    assert result.text.startswith("state=ROLLED_BACK")
    (alarm,) = ctx.telemetry.take_pending_alarms()
    assert alarm.kind is AlarmKind.POSTCONDITION_FAILED
    assert alarm.detail.startswith("GUI proposal msg_") and "[0] REGION_CHANGED" in alarm.detail


async def test_a_gui_alarm_names_the_recording_that_holds_its_evidence() -> None:
    ctx = _desk(recording_id="rec_42")

    await run_tool(ctx, "click", {"x": 5, "y": 5, "expect": {"region": "0,0,10,10"}})

    (alarm,) = ctx.telemetry.take_pending_alarms()
    assert " in recording rec_42 rolled back" in alarm.detail


async def test_cap_counts_any_other_rollback_toward_the_alarm_threshold() -> None:
    ctx = make_context()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(PostconditionKind.FILE_EXISTS, subject="other.txt"),),
        reason="a write whose postcondition names the wrong file",
    )

    assignment = make_assignment()
    proposal = make_proposal(ctx, assignment, request)

    outcome = await cap(ToolInvocation(ctx=ctx, assignment=assignment), proposal)

    assert outcome.state is ProposalState.ROLLED_BACK
    assert ctx.telemetry.take_pending_alarms() == ()  # One of three, not an Alarm yet.


async def test_a_judges_reject_is_a_failed_result_and_one_critical_alarm() -> None:
    verdict = _verdict(JudgeOutcome.REJECT, "it deleted the account", "nothing asked for that")
    ctx = _desk(_reviewed(verdict), recording_id="rec_7")

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "irreversible": True})

    assert result.is_error
    assert result.text.startswith(
        "judge=REJECT (it deleted the account; nothing asked for that); state=VERIFIED"
    )
    # The one Alarm for it (the gate only recorded the verdict), on the path that ends the attempt.
    (alarm,) = ctx.telemetry.take_pending_alarms()
    assert (alarm.kind, alarm.severity, alarm.reason) == (
        AlarmKind.AUDIT_FAILED,
        AlarmSeverity.CRITICAL,
        REVIEW_REJECTED_REASON,
    )
    assert alarm.detail.endswith(
        "in recording rec_7 was rejected on review: it deleted the account; nothing asked for that"
    )


@pytest.mark.parametrize("outcome", [JudgeOutcome.APPROVE, JudgeOutcome.REQUEST_CHANGES])
async def test_any_other_verdict_is_one_note_on_a_successful_result(outcome: JudgeOutcome) -> None:
    ctx = _desk(_reviewed(_verdict(outcome, "fine")))

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "irreversible": True})

    assert not result.is_error
    assert result.text.startswith("state=VERIFIED")
    assert result.text.endswith(f"; judge={outcome.value} (fine)")


async def test_a_judges_reasons_are_cut_to_the_review_bound() -> None:
    ctx = _desk(_reviewed(_verdict(JudgeOutcome.REJECT, "x" * 500, "y" * 500)))

    result = await run_tool(ctx, "click", {"x": 5, "y": 5, "irreversible": True})

    reasons = result.text.split("(", 1)[1].split(")", 1)[0]
    assert len(reasons) == MAX_REVIEW_CHARS + len("...")


async def test_tool_output_flags_every_outcome_but_verified_as_an_error() -> None:
    ctx = make_context()
    request = ProposalRequest(
        tier=RiskTier.SCRATCH_WRITE,
        action=make_action(),
        postconditions=(make_postcondition(),),
        reason="a test proposal",
    )

    assignment = make_assignment()
    proposal = make_proposal(ctx, assignment, request)

    outcome = await cap(ToolInvocation(ctx=ctx, assignment=assignment), proposal)

    assert outcome.state is ProposalState.VERIFIED
    assert tool_output(outcome).is_error is False
    rejected = outcome.model_copy(update={"state": ProposalState.REJECTED})
    assert tool_output(rejected).is_error is True


async def _cap_one(
    ctx: WorkerContext, tier: RiskTier, kind: ActionKind = ActionKind.DIFF, **fields: object
) -> ProposalState:
    """Propose one `kind` action at `tier` through `ctx`'s real gate; return the terminal state."""
    assignment = make_assignment()
    request = ProposalRequest(
        tier=tier, action=make_action(kind, **fields), postconditions=(), reason="a test proposal"
    )
    proposal = make_proposal(ctx, assignment, request)
    outcome = await cap(ToolInvocation(ctx=ctx, assignment=assignment), proposal)
    return outcome.state


async def _denials(ctx: WorkerContext) -> list[dict[str, object]]:
    """Every `guard.denied` payload on `ctx`'s trail, oldest first."""
    events = await ctx.trail.query(TrailQuery(kind="guard.denied"))
    return [dict(event.payload) for event in events]


async def test_an_outside_scratch_write_without_cell_outside_scratch_is_a_guard_denial() -> None:
    ctx = make_context(
        capabilities=CapabilitySet.parse("fs:write:/outside/**", "tool:*"),
        lease=FakeLeaseView(Path("scratch"), allowed_paths=(_OUTSIDE,)),
    )

    state = await _cap_one(ctx, RiskTier.OUTSIDE_SCRATCH_WRITE, paths=("/outside/note.txt",))

    assert state is ProposalState.REJECTED
    [denial] = await _denials(ctx)
    assert denial["point"] == "session_outside_scratch"
    assert denial["capability"] == "cell:outside_scratch:/outside/note.txt"
    assert denial["principal_id"] == ctx.worker_id


async def test_a_command_refused_its_exec_capability_is_a_tool_invocation_denial() -> None:
    ctx = make_context(capabilities=CapabilitySet.parse("exec:git", "tool:*"))

    state = await _cap_one(ctx, RiskTier.OUTSIDE_SCRATCH_WRITE, kind=ActionKind.COMMAND)

    assert state is ProposalState.REJECTED
    [denial] = await _denials(ctx)
    assert denial["point"] == "tool_invocation"
    assert denial["capability"] == "exec:true"


async def test_a_refusal_no_capability_names_records_no_guard_denial() -> None:
    # An unreachable path is a lease boundary, not a missing capability: Capping says so alone.
    ctx = make_context(capabilities=CapabilitySet.parse("fs:write:/elsewhere/**", "tool:*"))

    state = await _cap_one(ctx, RiskTier.OUTSIDE_SCRATCH_WRITE, paths=("/elsewhere/note.txt",))

    assert state is ProposalState.REJECTED
    assert await _denials(ctx) == []
