"""Turn a tool's request into a Proposal, run it through the Capping gate, and describe the result.

This is the one place a Worker tool's side effect turns into a `hivemind.supervision.capping.
Proposal` and a `hivemind.supervision.capping.GateOutcome` turns back into tool-result text
(codingrules section 8.12: "Propose, then commit... The CappingGate runs the checks the tier
requires, applies the proposal, verifies the postconditions, and rolls back on failure"). Every
other tool in `hivemind.workers.tools` that has a side effect (`write_file`, `run_command`,
`http_request`) builds its own `waggle.messages.capping.ProposedAction` and postconditions, then
calls `make_proposal` and `cap` here rather than talking to `ctx.capping` directly, so the id
minting, the Proposal's fixed fields (task id, Cell id, proposer, tempo, clearance, spend estimate)
and the human-readable outcome text are written once.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Called by
    `hivemind.workers.tools.session` and `hivemind.workers.tools.http`. Calls into `hivemind.cell`,
    `hivemind.forage.tempo`, `hivemind.supervision.capping`, `hivemind.workers.context`,
    `hivemind.workers.telemetry` (through `ctx.telemetry.note_alarm`, this dispatch's own fix 2)
    and waggle only.

Key invariants:
    - `make_proposal` never reads `ctx.capabilities` or `ctx.lease`: those are checked by
      `CappingGate.run` itself (through `cap`), never pre-filtered here, so a rejection is always
      the gate's own verdict, not a tool second-guessing it.
    - `spend_estimate_usd` is always 0.0 (roadmap step 3.16, v0): no built-in tool this phase
      proposes a real spend.
    - `describe` never includes a diff's or a command's own text, only ids, the tier-and-state
      verdict and each check/postcondition's outcome (codingrules section 12's "never the diff
      text back" carried over from the trail's own payload rule, even though this is tool-result
      text rather than a trail event).
    - `cap` notes exactly one Alarm per ROLLED_BACK outcome, never more: a Proposal only ever
      reaches ROLLED_BACK once (`hivemind.supervision.capping.state`'s own transition table has no
      edge back out of it).

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
from hivemind.supervision.capping import GateOutcome, Proposal, ProposalState, RiskTier
from hivemind.workers.context import WorkerContext
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


async def cap(ctx: WorkerContext, proposal: Proposal) -> GateOutcome:
    """Propose, then run, `proposal` through this attempt's Capping gate.

    A ROLLED_BACK outcome also notes an Alarm on `ctx.telemetry` (this dispatch's own fix 2):
    `cap` is the one place a tool's own call to the gate is, so it is the one place that can see
    both the real `GateOutcome` and this attempt's own tracker without either a tool or the gate
    itself needing a handle on the other (`hivemind.workers.runtime.WorkerRuntime` drains the note
    into a real `AlarmRaised` on its next tick).

    Args:
        ctx: This attempt's WorkerContext: supplies the gate, the capabilities to check the
            proposal against, the lease view for path reachability, and the telemetry tracker a
            rollback is noted on.
        proposal: A freshly built Proposal, from `make_proposal`.

    Returns:
        The gate's terminal outcome: VERIFIED, REJECTED or ROLLED_BACK.
    """
    await ctx.capping.propose(proposal)
    outcome = await ctx.capping.run(proposal.id, ctx.capabilities, ctx.lease)
    if outcome.state is ProposalState.ROLLED_BACK:
        ctx.telemetry.note_alarm(ROLLBACK_ALARM_KIND, outcome.reason)
    return outcome


def describe(outcome: GateOutcome) -> str:
    """Render a GateOutcome as tool-result text: state, reason, and every check/postcondition.

    Args:
        outcome: What `cap` returned.

    Returns:
        One line naming the terminal state, the reason, and each check kind and postcondition
        index with its own pass/fail -- never the proposal's diff or command text (module
        docstring).
    """
    checks = "; ".join(f"{check.kind.value}={check.outcome.value}" for check in outcome.checks)
    postconditions = "; ".join(
        f"[{pc.index}] {pc.kind.value}={'held' if pc.has_held else 'failed'}"
        for pc in outcome.postconditions
    )
    return (
        f"state={outcome.state.value}; reason={outcome.reason}; "
        f"checks=({checks or 'none'}); postconditions=({postconditions or 'none'})"
    )
