"""Unit tests for hivemind.workers.tools.proposals: make_proposal, cap, describe.

Roadmap step 10.3's own tests at the bottom: a Capping ALLOWLIST refusal that names a missing
capability is also the Guard's own `guard.denied`, at `session_outside_scratch` for a write leaving
scratch and at `tool_invocation` for anything else; a refusal no capability names records nothing.
"""

from __future__ import annotations

from pathlib import Path

from builders.capping import FakeLeaseView, make_action, make_postcondition
from builders.workers import make_assignment, make_context

from hivemind.guard import CapabilitySet
from hivemind.pheromone import TrailQuery
from hivemind.supervision.capping import ProposalState, RiskTier
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.proposals import ProposalRequest, cap, describe, make_proposal
from hivemind.workers.tools.registry import ToolInvocation
from waggle.messages.capping import ActionKind

_OUTSIDE = Path("/outside")  # A root the lease reaches beyond scratch, like a declared keep_root.


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
