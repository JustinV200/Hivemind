"""Unit tests for hivemind.workers.tools.proposals: make_proposal, cap, describe."""

from __future__ import annotations

from builders.capping import make_action, make_postcondition
from builders.workers import make_assignment, make_context

from hivemind.supervision.capping import ProposalState, RiskTier
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal


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

    outcome = await cap(ctx, proposal)

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

    outcome = await cap(ctx, proposal)
    text = describe(outcome)

    assert "top distinctive line" not in text
    assert "state=" in text and "reason=" in text
