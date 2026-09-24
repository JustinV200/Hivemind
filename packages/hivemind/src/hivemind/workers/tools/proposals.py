"""Turn a tool's request into a Proposal, run it through the Capping gate, and describe the result.

This is the one place a Worker tool's side effect turns into a `hivemind.supervision.capping.
Proposal` and a `hivemind.supervision.capping.GateOutcome` turns back into tool-result text
(codingrules section 8.12: "Propose, then commit... The CappingGate runs the checks the tier
requires, applies the proposal, verifies the postconditions, and rolls back on failure"). Every
other tool in `hivemind.workers.tools` that has a side effect (`write_file`, `run_command`,
`http_request`) builds its own `waggle.messages.capping.ProposedAction` and postconditions, then
calls `make_proposal` and `cap` here rather than talking to `ctx.capping` directly, so the id
minting, the Proposal's fixed fields (task id, Cell id, proposer, tempo, clearance, spend estimate)
and the human-readable outcome text are written once. Roadmap step 10.3 (ADR-0031): the gate's
ALLOWLIST rung is a capability check, so when it refuses because the Worker's set lacks one
capability (`CheckResultRecord.denied_capability`), `cap` also records that refusal through the
Guard's `Enforcer` as `guard.denied` -- at `session_outside_scratch` for an outside-scratch write,
at `tool_invocation` for anything else (a command's `exec`, a network step's `net`) -- so every
capability refusal a Worker meets is on the trail as a Guard decision, not only as `capping.*`.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Called by
    `hivemind.workers.tools.session`, `.keep` and `.http`. Calls into `hivemind.cell`,
    `hivemind.forage.tempo`, `hivemind.guard`, `hivemind.supervision.capping`,
    `hivemind.workers.context`, `hivemind.workers.telemetry` (through `ctx.telemetry.note_alarm`,
    this dispatch's own fix 2), `hivemind.workers.tools.authorize`, `hivemind.workers.tools.
    registry` (ToolInvocation) and waggle only.

Key invariants:
    - `make_proposal` never reads `ctx.capabilities` or `ctx.lease`: those are checked by
      `CappingGate.run` itself (through `cap`), never pre-filtered here, so a rejection is always
      the gate's own verdict, not a tool second-guessing it.
    - `spend_estimate_usd` is always 0.0 (roadmap step 3.16, v0): no built-in tool this phase
      proposes a real spend.
    - `describe` never includes a diff's or a command's own text, only ids, the tier-and-state
      verdict, each check/postcondition's outcome and (roadmap step 5.0e) each outside-scratch
      path's own leave verdict -- a resolved path and a one-line reason, never file contents
      (codingrules section 12's "never the diff text back" carried over from the trail's own
      payload rule, even though this is tool-result text rather than a trail event).
    - `cap` notes exactly one Alarm per ROLLED_BACK outcome, never more: a Proposal only ever
      reaches ROLLED_BACK once (`hivemind.supervision.capping.state`'s own transition table has no
      edge back out of it).
    - `cap` records at most one `guard.denied` per proposal, and only for a REJECTED outcome whose
      failing check named a missing capability: the Guard re-checks that capability against the
      same set, so it is recorded only when the Guard agrees it is not held.

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit `ProposalRequest` exists
      to keep `make_proposal` under (a bare `ctx, assignment, tier, action, postconditions, reason`
      signature would be six parameters, one over the hard limit).
    - .claude/codingrules.md section 8.12 for "Propose, then commit."
    - hivemind.supervision.capping for Proposal, CappingGate.propose/run and GateOutcome.
    - hivemind.workers.tools.session and .http for this module's two callers.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.forage.tempo import Tempo
from hivemind.guard import Capability, CapabilityFamily, EnforcementPoint, InvalidCapabilityError
from hivemind.supervision.capping import GateOutcome, Proposal, ProposalState, RiskTier
from hivemind.supervision.capping.leave import LeaveDecisionRecord
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.authorize import authorize
from hivemind.workers.tools.registry import ToolInvocation
from waggle.ids import new_message_id
from waggle.messages.capping import ProposedAction
from waggle.messages.labels import Postcondition
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign

MIN_SPEND_ESTIMATE_USD = 0.0  # v0: no built-in tool proposes a real spend (module docstring).
# POSTCONDITION_FAILED already names exactly this: "a proposal's declared postcondition did not
# hold after applying" (hivemind.supervision.alarm.AlarmKind's own docstring) is ROLLED_BACK's own
# definition (hivemind.supervision.capping.gate's module docstring); no new AlarmKind is needed.
ROLLBACK_ALARM_KIND = AlarmKind.POSTCONDITION_FAILED
# Roadmap step 10.3: the families a path refusal names. An outside-scratch write refused on one of
# them is the session_outside_scratch point; every other allowlist refusal (a command's `exec`, a
# network step's `net`, a path inside scratch) is the tool's own call, tool_invocation.
_PATH_FAMILIES = frozenset(
    {CapabilityFamily.FS_READ, CapabilityFamily.FS_WRITE, CapabilityFamily.CELL_OUTSIDE_SCRATCH}
)

__all__ = ["ROLLBACK_ALARM_KIND", "ProposalRequest", "cap", "describe", "make_proposal"]


@dataclass(frozen=True, slots=True)
class ProposalRequest:
    """What a tool wants to do: everything `make_proposal` needs beyond `ctx` and `assignment`.

    Bundled (codingrules section 5.1) so `make_proposal` stays at three parameters instead of six.
    """

    tier: RiskTier  # Declared from the action's own reach (inside or outside scratch).
    action: ProposedAction  # The diff, command or sequence to run.
    postconditions: tuple[Postcondition, ...]  # What the caller expects to hold once applied.
    reason: str  # Why the tool wants to do this, in one line.


def make_proposal(ctx: WorkerContext, assignment: TaskAssign, request: ProposalRequest) -> Proposal:
    """Build a fresh Proposal for `request.action`, with every id and stamp a tool need not repeat.

    Args:
        ctx: This attempt's WorkerContext: supplies the proposer id, Cell id and clock.
        assignment: The task this action serves: supplies the task id, tempo and clearance.
        request: The tier, action, postconditions and reason a tool wants proposed.

    Returns:
        A validated Proposal, in `ProposalState.PROPOSED`, ready for `cap`.
    """
    return Proposal(
        id=new_message_id(ctx.clock),
        task_id=assignment.task_id,
        cell_id=ctx.cell.id,
        proposer=ctx.worker_id,
        risk_tier=request.tier,
        action=request.action,
        postconditions=request.postconditions,
        tempo=Tempo.from_wire(assignment.tempo),
        spend_estimate_usd=MIN_SPEND_ESTIMATE_USD,
        clearance=HoneyClearance.from_wire(assignment.clearance),
        reason=request.reason,
        state=ProposalState.PROPOSED,
    )


async def cap(invocation: ToolInvocation, proposal: Proposal) -> GateOutcome:
    """Propose, then run, `proposal` through this attempt's Capping gate.

    A ROLLED_BACK outcome is also counted on `ctx.telemetry`, which queues an Alarm once this
    attempt has had `hivemind.workers.telemetry.ROLLBACKS_BEFORE_ALARM` of them (fix 2, revised):
    `cap` is the one place a tool's own call to the gate is, so it is the one place that can see
    both the real `GateOutcome` and this attempt's own tracker without either a tool or the gate
    itself needing a handle on the other (`hivemind.workers.runtime.WorkerRuntime` drains the note
    into a real `AlarmRaised` on its next tick).

    Args:
        invocation: This attempt's context and assignment. `ctx` supplies the gate, the
            capabilities to check the proposal against, the lease view for path reachability, the
            telemetry tracker a rollback is noted on, the Enforcer an allowlist refusal is recorded
            through (roadmap step 10.3), and (roadmap step 5.0d) `ctx.asker`, the real
            transport-backed asker `WorkerRuntime` has already substituted in by the time a tool
            call runs, so an ASK-verdict leaving can raise its Question up the same Worker ->
            Warden -> Queen chain `hivemind.workers.tools.ask.ask` uses.
        proposal: A freshly built Proposal, from `make_proposal`.

    Returns:
        The gate's terminal outcome: VERIFIED, REJECTED or ROLLED_BACK.
    """
    ctx = invocation.ctx
    await ctx.capping.propose(proposal)
    outcome = await ctx.capping.run(proposal.id, ctx.capabilities, ctx.lease, ctx.asker)
    if outcome.state is ProposalState.ROLLED_BACK:
        ctx.telemetry.note_rollback(ROLLBACK_ALARM_KIND, outcome.reason)
    if outcome.state is ProposalState.REJECTED:
        await _record_refusal(invocation, proposal, outcome)
    return outcome


async def _record_refusal(
    invocation: ToolInvocation, proposal: Proposal, outcome: GateOutcome
) -> None:
    """Record the gate's capability refusal, if it was one, as the Guard's own `guard.denied`."""
    denied = next((c.denied_capability for c in outcome.checks if c.denied_capability), None)
    if denied is None:
        return  # Refused for a reason no capability names (a schema, a size, a path's reach).
    try:
        needed = Capability.parse(denied)
    except InvalidCapabilityError:
        return  # The gate built this string from a Capability; this is unreachable defence.
    # Leaving scratch is its own point; every other allowlist refusal is the tool's own call.
    leaving = proposal.risk_tier is RiskTier.OUTSIDE_SCRATCH_WRITE
    point = (
        EnforcementPoint.SESSION_OUTSIDE_SCRATCH
        if leaving and needed.family in _PATH_FAMILIES
        else EnforcementPoint.TOOL_INVOCATION
    )
    await authorize(invocation, point, needed)


def describe(outcome: GateOutcome) -> str:
    """Render a GateOutcome as tool-result text: state, reason, and every check/postcondition.

    Args:
        outcome: What `cap` returned.

    Returns:
        One line naming the terminal state, the reason, each check kind and postcondition index
        with its own pass/fail, and (only when the proposal touched a path outside scratch,
        roadmap step 5.0e) each such path's own leave verdict and whether it will actually remain
        -- never the proposal's diff or command text (module docstring).
    """
    checks = "; ".join(f"{check.kind.value}={check.outcome.value}" for check in outcome.checks)
    postconditions = "; ".join(
        f"[{pc.index}] {pc.kind.value}={'held' if pc.has_held else 'failed'}"
        for pc in outcome.postconditions
    )
    text = (
        f"state={outcome.state.value}; reason={outcome.reason}; "
        f"checks=({checks or 'none'}); postconditions=({postconditions or 'none'})"
    )
    if outcome.leave_decisions:
        text += f"; leaves=({_describe_leaves(outcome.leave_decisions)})"
    return text


def _describe_leaves(decisions: tuple[LeaveDecisionRecord, ...]) -> str:
    """Render each outside-scratch path's own leave verdict, plainly: will it remain, and why."""
    return "; ".join(
        f"{decision.path}="
        f"{'will remain' if decision.persisted else 'will be removed on release'} "
        f"({decision.reason})"
        for decision in decisions
    )
