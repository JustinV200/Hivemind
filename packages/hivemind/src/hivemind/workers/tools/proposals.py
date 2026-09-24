"""Turn a tool's request into a Proposal, run it through the Capping gate, and describe the result.

This is the one place a Worker tool's side effect turns into a `hivemind.supervision.capping.
Proposal` and a `hivemind.supervision.capping.GateOutcome` turns back into tool-result text
(codingrules section 8.12: "Propose, then commit... The CappingGate runs the checks the tier
requires, applies the proposal, verifies the postconditions, and rolls back on failure"). Every
other tool in `hivemind.workers.tools` that has a side effect (`write_file`, `run_command`,
`http_request`, and the Exoskeleton's action tools) builds its own `waggle.messages.capping.
ProposedAction` and postconditions, then calls `make_proposal` and `cap` here rather than talking
to `ctx.capping` directly, so the id minting, the Proposal's fixed fields (task id, Cell id,
proposer, tempo, clearance, spend estimate), what a rollback raises and the human-readable outcome
text are written once. A rolled-back GUI action (roadmap step 6.5, ADR-0032) raises its Alarm at
once rather than after three: the screen no longer matches what the bee believes, so every later
step would act on a misread. An applied irreversible GUI action also comes back with the judge's
review (`GateOutcome.review`): a REJECT becomes a failed result (`tool_output`) and a CRITICAL
AUDIT_FAILED Alarm, whose escalation ends the attempt (ADR-0032).

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Called by
    `hivemind.workers.tools.session`, `.http`, `.keep` and the `.exoskeleton` action tools. Calls
    into `hivemind.cell`, `hivemind.forage.tempo`, `hivemind.supervision.capping`,
    `hivemind.workers.context`, `hivemind.workers.telemetry` (through `ctx.telemetry.note_alarm`
    and `note_rollback`), `hivemind.workers.tools.registry` (ToolOutput) and waggle only.

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
    - `cap` notes every ROLLED_BACK outcome exactly once, never more: a Proposal only ever
      reaches ROLLED_BACK once (`hivemind.supervision.capping.state`'s own transition table has no
      edge back out of it). A GUI action's rollback is an Alarm at once; any other kind's counts
      toward `hivemind.workers.telemetry.ROLLBACKS_BEFORE_ALARM`.
    - A judge's REJECT is noted as exactly one CRITICAL AUDIT_FAILED Alarm, here: the gate only
      records the verdict, so the Warden's policy (escalation to a person) ends the attempt.

See Also:
    - .claude/codingrules.md section 5.1 for the parameter-count limit `ProposalRequest` exists
      to keep `make_proposal` under (a bare `ctx, assignment, tier, action, postconditions, reason`
      signature would be six parameters, one over the hard limit).
    - .claude/codingrules.md section 8.12 for "Propose, then commit."
    - hivemind.supervision.capping for Proposal, CappingGate.propose/run and GateOutcome.
    - hivemind.workers.tools.session, .http, .keep and .exoskeleton for this module's callers.
    - docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md for the
      immediate GUI Alarm and the judged irreversible GUI action.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.forage.tempo import Tempo
from hivemind.supervision.capping import (
    GateOutcome,
    JudgeOutcome,
    JudgeVerdict,
    Proposal,
    ProposalState,
    RiskTier,
)
from hivemind.supervision.capping.leave import LeaveDecisionRecord
from hivemind.workers.context import WorkerContext
from hivemind.workers.tools.registry import ToolOutput
from waggle.ids import new_message_id
from waggle.messages import AlarmSeverity
from waggle.messages.capping import ActionKind, ProposedAction
from waggle.messages.labels import Postcondition
from waggle.messages.supervision import AlarmKind
from waggle.messages.task import TaskAssign

MIN_SPEND_ESTIMATE_USD = 0.0  # v0: no built-in tool proposes a real spend (module docstring).
# POSTCONDITION_FAILED already names exactly this: "a proposal's declared postcondition did not
# hold after applying" (hivemind.supervision.alarm.AlarmKind's own docstring) is ROLLED_BACK's own
# definition (hivemind.supervision.capping.gate's module docstring); no new AlarmKind is needed.
ROLLBACK_ALARM_KIND = AlarmKind.POSTCONDITION_FAILED
MAX_REVIEW_CHARS = 600  # A judge's reasons as the model reads them: a few sentences, never notes.
# Why a Worker escalates a judged irreversible GUI action: it already happened and cannot be undone.
REVIEW_REJECTED_REASON = "A judge rejected an irreversible GUI action after it was applied."

__all__ = [
    "MAX_REVIEW_CHARS",
    "REVIEW_REJECTED_REASON",
    "ROLLBACK_ALARM_KIND",
    "ProposalRequest",
    "cap",
    "describe",
    "make_proposal",
    "tool_output",
]


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

    A ROLLED_BACK outcome is also counted on `ctx.telemetry`, which queues an Alarm once this
    attempt has had `hivemind.workers.telemetry.ROLLBACKS_BEFORE_ALARM` of them (fix 2, revised):
    `cap` is the one place a tool's own call to the gate is, so it is the one place that can see
    both the real `GateOutcome` and this attempt's own tracker without either a tool or the gate
    itself needing a handle on the other (`hivemind.workers.runtime.WorkerRuntime` drains the note
    into a real `AlarmRaised` on its next tick).

    Args:
        ctx: This attempt's WorkerContext: supplies the gate, the capabilities to check the
            proposal against, the lease view for path reachability, the telemetry tracker a
            rollback is noted on, and (roadmap step 5.0d) `ctx.asker`, the real transport-backed
            asker `WorkerRuntime` has already substituted in by the time a tool call runs, so an
            ASK-verdict leaving can raise its Question up the same Worker -> Warden -> Queen chain
            `hivemind.workers.tools.ask.ask` uses.
        proposal: A freshly built Proposal, from `make_proposal`.

    Returns:
        The gate's terminal outcome: VERIFIED, REJECTED or ROLLED_BACK.
    """
    await ctx.capping.propose(proposal)
    outcome = await ctx.capping.run(proposal.id, ctx.capabilities, ctx.lease, ctx.asker)
    if outcome.state is ProposalState.ROLLED_BACK:
        _note_rolled_back(ctx, proposal, outcome)
    elif outcome.review is not None and outcome.review.outcome is JudgeOutcome.REJECT:
        # ADR-0032: a rejection raises an Alarm and ends the attempt. Through the Warden, whose
        # policy acts on it (AUDIT_FAILED escalates to a person); the gate only recorded the
        # verdict, so this is the one Alarm for it.
        detail = f"{_gui_subject(ctx, proposal)} was rejected on review: {_reasons(outcome.review)}"
        ctx.telemetry.note_alarm(
            AlarmKind.AUDIT_FAILED,
            detail,
            reason=REVIEW_REJECTED_REASON,
            severity=AlarmSeverity.CRITICAL,
        )
    return outcome


def tool_output(outcome: GateOutcome) -> ToolOutput:
    """Render a GateOutcome as a whole tool result: its text, and whether the action failed.

    Args:
        outcome: What `cap` returned.

    Returns:
        `describe`'s text, flagged as an error unless the proposal VERIFIED and no judge rejected
        it; a judge's REJECT leads the text with the rejection and its reasons, so the model reads
        first that the action it believes succeeded was refused on review (the gate has already
        raised that Alarm).
    """
    text = describe(outcome)
    review = outcome.review
    if review is not None and review.outcome is JudgeOutcome.REJECT:
        return ToolOutput(text=f"judge=REJECT ({_reasons(review)}); {text}", is_error=True)
    return ToolOutput(text=text, is_error=outcome.state is not ProposalState.VERIFIED)


def describe(outcome: GateOutcome) -> str:
    """Render a GateOutcome as tool-result text: state, reason, and every check/postcondition.

    Args:
        outcome: What `cap` returned.

    Returns:
        One line naming the terminal state, the reason, each check kind and postcondition index
        with its own pass/fail, (only when the proposal touched a path outside scratch, roadmap
        step 5.0e) each such path's own leave verdict and whether it will actually remain, and
        (only for a judged irreversible GUI action the judge did not reject, roadmap step 6.5)
        the judge's verdict and reasons -- never the proposal's diff, command or typed text
        (module docstring). A REJECT is `tool_output`'s to lead with, not this line's.
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
    if outcome.review is not None and outcome.review.outcome is not JudgeOutcome.REJECT:
        # APPROVE or REQUEST_CHANGES on an applied irreversible GUI action: one line of verdict.
        text += f"; judge={outcome.review.outcome.value} ({_reasons(outcome.review)})"
    return text


def _note_rolled_back(ctx: WorkerContext, proposal: Proposal, outcome: GateOutcome) -> None:
    """Note one rollback: an Alarm at once for a GUI action, else one more toward the count."""
    if proposal.action.kind is ActionKind.GUI:
        # ADR-0032: a GUI action whose declared postcondition failed raises an Alarm at once, not
        # after three, since every later step would act on a screen the bee has misread; a step
        # that failed outright leaves the screen just as unknown, so it alarms the same way.
        ctx.telemetry.note_alarm(ROLLBACK_ALARM_KIND, _gui_alarm_detail(ctx, proposal, outcome))
        return
    ctx.telemetry.note_rollback(ROLLBACK_ALARM_KIND, outcome.reason)


def _gui_alarm_detail(ctx: WorkerContext, proposal: Proposal, outcome: GateOutcome) -> str:
    """Name the rolled-back GUI proposal, the gate's reason and each postcondition that failed."""
    failed = ", ".join(
        f"[{pc.index}] {pc.kind.value}" for pc in outcome.postconditions if not pc.has_held
    )
    detail = f"{_gui_subject(ctx, proposal)} rolled back: {outcome.reason}"
    return f"{detail} (failed: {failed})" if failed else detail


def _gui_subject(ctx: WorkerContext, proposal: Proposal) -> str:
    """Name a GUI proposal and the recording that holds its evidence, for an Alarm's detail.

    ADR-0032: the Alarm names the recording, never a frame; the recording keys the action by the
    proposal id, so the two together point at the evidence without carrying any of it.
    """
    recording = f" in recording {ctx.recording_id}" if ctx.recording_id is not None else ""
    return f"GUI proposal {proposal.id}{recording}"


def _reasons(review: JudgeVerdict) -> str:
    """Join a judge's reasons into one bounded line; "no reason given" when it gave none."""
    joined = "; ".join(review.reasons) or "no reason given"
    return joined if len(joined) <= MAX_REVIEW_CHARS else f"{joined[:MAX_REVIEW_CHARS]}..."


def _describe_leaves(decisions: tuple[LeaveDecisionRecord, ...]) -> str:
    """Render each outside-scratch path's own leave verdict, plainly: will it remain, and why."""
    return "; ".join(
        f"{decision.path}="
        f"{'will remain' if decision.persisted else 'will be removed on release'} "
        f"({decision.reason})"
        for decision in decisions
    )
