"""Define JudgeCheck, JudgeReviewer and JudgeVerdict: the Capping gate's independent-review rung.

Codingrules section 8.12: "Judge review runs on ModelSlot.JUDGE with a rubric and no shared
context with the proposer, and the manifest may pin it to a different provider so blind spots do
not correlate. Verdicts are structured: approve, request changes, reject, with reasons."
`ModelSlot` (`hivemind.forage.slots`, a named place a model call resolves to) is how a manifest
names which provider and model answer `JUDGE` calls; that pin already works through the manifest's
`[llm.slots]` table (`worker = {...}`, `judge = {provider = "another-provider", ...}`) -- nothing
in this module builds or needs it. This package never imports `hivemind.llm` (an autopilot
boundary, codingrules section 4: `hivemind.queen.autopilot` and `hivemind.wardens.autopilot` both
import `hivemind.supervision`, and neither may reach `hivemind.llm` even transitively), so it never
resolves a slot to a model itself. Instead `JudgeReviewer` is the Protocol seam (codingrules
section 8.1) a Warden-layer implementation satisfies in a later dispatch by actually calling
`complete_structured` on `ModelSlot.JUDGE`; this module ships the seam, `JudgeCheck` (the `Check`
rung that calls it), and the request/verdict shapes, so a composition root can wire a real reviewer
in without this package ever seeing a model.

"No shared context with the proposing bee" is `JudgeRequest`'s whole shape: the proposal's content
(`waggle.messages.capping.ProposedAction`, carried directly -- a value model with no behaviour) and
its declared postconditions (the task's acceptance criteria, in the same `Postcondition` shape a
`Proposal` already carries), plus the tier's rubric. Nothing here names the proposer, carries its
transcript, or reaches into its hot state (Bee Bread, the Hive's warm memory tier) -- a judge
reviews the work, never who did it or what they were thinking while doing it.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.supervision.capping.
    checks`. `judge_checks()`, this module's sibling to `hivemind.supervision.capping.checks.
    deterministic.deterministic_checks()`, is called by whichever composition root has a
    `JudgeReviewer` to wire in (a later dispatch that builds the model-backed one; this phase
    reports the one-line merge `cli/compose/deps.py` needs, `{**deterministic_checks(),
    **judge_checks(reviewer, rubrics)}`); run by `hivemind.supervision.capping.gate.CappingGate`
    like any other Check. Calls into `hivemind.supervision.capping.checks.base`, `hivemind.
    supervision.capping.checks.rubrics`, `hivemind.supervision.capping.errors`, `hivemind.
    supervision.capping.tiers` and waggle only.

Key invariants:
    - JudgeVerdict and JudgeRequest are frozen and forbid extras, like every boundary value here.
    - JudgeCheck.run never awaits hivemind.llm; it awaits only the injected JudgeReviewer, so this
      module stays importable from an autopilot package (codingrules section 4).
    - A tier with no configured rubric fails JudgeCheck closed (FAILED), never silently PASSED --
      the same fail-closed shape CappingGate.run already applies to a missing Check implementation
      (docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md).
    - A JudgeReviewer that raises JudgeAnswerError (it could not produce a verdict at all, e.g. a
      model's structured-output ladder exhausted every retry) never propagates out of JudgeCheck.
      run: it becomes a FAILED CheckResultRecord with judge_error=True, so a judge that cannot
      answer rejects the proposal instead of crashing the Worker (2026-09-21, the real trail this
      fixes: nine unparseable llm.call attempts reached worker.failed / WORKER_CRASHED).

See Also:
    - .claude/codingrules.md section 8.12 for the judge-independence rule this module implements.
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule JudgeReviewer follows.
    - hivemind.supervision.capping.checks.rubrics for JudgeRubric and load_judge_rubrics.
    - hivemind.supervision.capping.checks.fake for FakeJudgeReviewer, a scripted JudgeReviewer.
    - hivemind.supervision.capping.errors for JudgeAnswerError, which a JudgeReviewer raises
      instead of answering and this module's run() catches.
    - hivemind.forage.slots for ModelSlot.JUDGE, which a manifest's [llm.slots] pins independently
      of ModelSlot.WORKER so blind spots do not correlate (a manifest edit, not code this package
      touches).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Annotated, ClassVar, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hivemind.forage.tempo import Tempo
from hivemind.supervision.capping.checks.base import Check, CheckContext, CheckResultRecord
from hivemind.supervision.capping.checks.rubrics import JudgeRubric
from hivemind.supervision.capping.errors import JudgeAnswerError
from hivemind.supervision.capping.tiers import RiskTier
from waggle.messages.capping import CheckKind, CheckOutcome, ProposedAction
from waggle.messages.labels import Postcondition

MAX_JUDGE_REASONS = 8  # A rubric names a handful of criteria; more than this is not "reasons".
MAX_JUDGE_REASON_CHARS = 500  # One sentence or two per reason.
MAX_JUDGE_NOTES_CHARS = 2_000  # Capped free text: a paragraph, never a transcript.

MAX_EVIDENCE_CHARS = 30_000  # Steps, two URLs, two bounded snapshots and the postconditions.
MAX_EVIDENCE_FRAMES = 2  # Before and after.
MAX_GOAL_CHARS = 4_000  # A task's objective, cut to this when judged: the brief, not a document.

__all__ = [
    "MAX_EVIDENCE_CHARS",
    "MAX_EVIDENCE_FRAMES",
    "MAX_GOAL_CHARS",
    "JudgeCheck",
    "JudgeEvidence",
    "JudgeOutcome",
    "JudgeRequest",
    "JudgeReviewer",
    "JudgeVerdict",
    "judge_checks",
]

# A single reason, bounded so a verdict's own text budget is fixed regardless of how many reasons
# a reviewer returns; MAX_JUDGE_REASONS bounds the count, this bounds each entry.
_Reason = Annotated[str, Field(max_length=MAX_JUDGE_REASON_CHARS)]


class JudgeOutcome(Enum):
    """The judge's structured decision on one proposal, mapped onto the check ladder's own shape.

    `hivemind.supervision.capping.checks.judge._OUTCOME_MAP` carries the mapping onto
    `waggle.messages.capping.CheckOutcome`, which has no independent APPROVE/REJECT vocabulary of
    its own -- CheckOutcome is shared by every rung of the ladder, JudgeOutcome only by this one.
    """

    APPROVE = "APPROVE"  # Maps to CheckOutcome.PASSED.
    REQUEST_CHANGES = "REQUEST_CHANGES"  # Maps to CheckOutcome.CHANGES_REQUESTED.
    REJECT = "REJECT"  # Maps to CheckOutcome.FAILED.


class JudgeVerdict(BaseModel):
    """One judge review's structured word: approve, request changes or reject, with reasons."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: JudgeOutcome = Field(description="The judge's decision.")
    reasons: tuple[_Reason, ...] = Field(
        max_length=MAX_JUDGE_REASONS,
        description="Why, as a short list; empty only when outcome is APPROVE with nothing to "
        "flag.",
    )
    rubric_id: str = Field(description="Which JudgeRubric this verdict was scored against.")
    notes: str = Field(
        default="",
        max_length=MAX_JUDGE_NOTES_CHARS,
        description="Capped free text for anything the rubric's own criteria do not capture.",
    )


class JudgeEvidence(BaseModel):
    """What an applied action did, for a judge to review after the fact (ADR-0032).

    For a GUI proposal this is the flight recorder's record of it: the steps (secrets as a
    length), the page URL and accessibility snapshot before and after, each postcondition with
    what was observed, rendered as `text`; and the before and after screens as PNG `frames`,
    which only a judge whose model declares vision is shown.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str = Field(max_length=MAX_EVIDENCE_CHARS, description="The structural evidence.")
    frames: tuple[bytes, ...] = Field(
        default=(),
        max_length=MAX_EVIDENCE_FRAMES,
        repr=False,
        description="PNG screens, before then after; never logged.",
    )


class JudgeRequest(BaseModel):
    """Everything an independent judge review needs about one proposal, and nothing else.

    Deliberately excludes the proposer's id, its transcript, its hot state and any Bee Bread
    (codingrules section 8.12: "no shared context with the proposing bee"): a judge reviews the
    proposed action and what it must satisfy, never who proposed it or what they were thinking.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_tier: RiskTier = Field(description="The proposal's declared tier.")
    action: ProposedAction = Field(
        description="The diff, command or sequence to review (waggle's own shape, carried "
        "directly)."
    )
    acceptance_criteria: tuple[Postcondition, ...] = Field(
        description="The task's acceptance criteria: the proposal's own stated postconditions."
    )
    rubric: JudgeRubric = Field(description="What to check for, at this proposal's risk tier.")
    tempo: Tempo = Field(
        default_factory=Tempo,
        description="The proposing task's speed-against-accuracy setting: orders the judge's own "
        "model call in the Fanner's queues; never changes what is reviewed.",
    )
    evidence: JudgeEvidence | None = Field(
        default=None,
        description="What the action did once applied (roadmap step 6.6): set when an applied "
        "irreversible GUI action is judged before the bee's next step; None for a review that "
        "happens before applying.",
    )
    goal: str | None = Field(
        default=None,
        max_length=MAX_GOAL_CHARS,
        description="The task's objective as the Queen planned it, so an applied action can be "
        "judged against what the task asked for; never the proposer's own reasoning.",
    )


class JudgeReviewer(Protocol):
    """Review one proposal independently of the bee that proposed it, and give a structured verdict.

    Implementations must be safe to call concurrently: a Warden may run more than one proposal's
    checks at once. The model-backed implementation (running `complete_structured` on
    `ModelSlot.JUDGE`) is built at the Warden layer in a later dispatch, because this package never
    imports `hivemind.llm` (codingrules section 4); `hivemind.supervision.capping.checks.fake.
    FakeJudgeReviewer` is this phase's own implementation, for tests and demo paths.
    """

    async def review(self, request: JudgeRequest) -> JudgeVerdict:
        """Score `request` against its rubric and return a structured verdict.

        Args:
            request: The proposal's content, tier, rubric and acceptance criteria; never the
                proposer's transcript or hot state.

        Returns:
            The judge's verdict: approve, request changes or reject, with reasons.
        """
        ...


class JudgeCheck:
    """The Check-ladder rung that runs an independent JudgeReviewer against a proposal's rubric."""

    kind: ClassVar[CheckKind] = CheckKind.JUDGE

    def __init__(self, reviewer: JudgeReviewer, rubrics: dict[RiskTier, JudgeRubric]) -> None:
        """Build a JudgeCheck.

        Args:
            reviewer: The independent reviewer this check calls; a FakeJudgeReviewer in tests, a
                model-backed one (a later dispatch) in production.
            rubrics: Every configured tier's rubric, from `checks.rubrics.load_judge_rubrics()`.
        """
        self._reviewer = reviewer
        self._rubrics = rubrics

    async def run(self, context: CheckContext) -> CheckResultRecord:
        """Build a no-shared-context JudgeRequest from `context` and score the reviewer's verdict.

        Args:
            context: The proposal plus everything needed to judge it; only `proposal.risk_tier`,
                `proposal.action`, `proposal.postconditions` and the task's `goal` ever reach the
                reviewer.

        Returns:
            FAILED with reason "no rubric configured for tier" when this tier has no rubric;
            FAILED with `judge_error=True` when the reviewer raised instead of answering (2026-09-
            21: a judge that cannot produce a verdict is a check outcome, not a bee crash);
            otherwise the reviewer's verdict, mapped onto CheckOutcome.
        """
        rubric = self._rubrics.get(context.proposal.risk_tier)
        if rubric is None:
            # Fail closed (codingrules 8.12), the same shape CappingGate.run applies to a check
            # kind its own registry does not implement: an unconfigured rubric never means "skip".
            return CheckResultRecord(
                kind=CheckKind.JUDGE,
                outcome=CheckOutcome.FAILED,
                reason="no rubric configured for tier",
            )
        request = JudgeRequest(
            risk_tier=context.proposal.risk_tier,
            action=context.proposal.action,
            acceptance_criteria=context.proposal.postconditions,
            rubric=rubric,
            tempo=context.proposal.tempo,
            # The task's objective, cut to the judge's bound; never the proposer's own reasoning.
            goal=context.goal[:MAX_GOAL_CHARS] if context.goal is not None else None,
        )
        return await _review_safely(self._reviewer, request)


def judge_checks(
    reviewer: JudgeReviewer, rubrics: dict[RiskTier, JudgeRubric]
) -> Mapping[CheckKind, Check]:
    """Build the composition root's judge check registry: CheckKind.JUDGE -> JudgeCheck.

    The sibling of `hivemind.supervision.capping.checks.deterministic.deterministic_checks()`: a
    composition root that has built a model-backed `JudgeReviewer` (a later dispatch, at the
    Warden layer, since this package never imports `hivemind.llm`) merges this mapping's one entry
    into the same `Mapping[CheckKind, Check]` a Warden's `GateDeps.checks` is built from --
    `{**deterministic_checks(), **judge_checks(reviewer, rubrics)}` -- rather than editing
    `deterministic_checks()` itself, which stays autopilot-only, no model, forever.

    Args:
        reviewer: The independent reviewer every `CheckKind.JUDGE` check calls.
        rubrics: Every configured tier's rubric, from `checks.rubrics.load_judge_rubrics()`.

    Returns:
        `{CheckKind.JUDGE: JudgeCheck(reviewer, rubrics)}`.
    """
    return {CheckKind.JUDGE: JudgeCheck(reviewer, rubrics)}


async def _review_safely(reviewer: JudgeReviewer, request: JudgeRequest) -> CheckResultRecord:
    """Call `reviewer.review(request)` and translate its outcome, or its JudgeAnswerError, alike.

    Split out of `JudgeCheck.run` (codingrules 5.1: functions stay under 50 lines).
    """
    # Latency class: one model call, typically seconds to tens of seconds; a timeout and retry
    # policy belong to the model-backed JudgeReviewer implementation, not this rung.
    try:
        verdict = await reviewer.review(request)
    except JudgeAnswerError as exc:
        # The reviewer could not produce a verdict at all (2026-09-21: nine unparseable llm.call
        # attempts on the judge lane propagated all the way to worker.failed / alarm.raised
        # WORKER_CRASHED before this existed). Reject the proposal with a readable reason instead
        # of letting the exception kill the Worker; judge_error=True tells CappingGate to record
        # this distinctly from an ordinary REJECT verdict on the capping.checked trail event. A
        # reviewer's ProviderUnavailableError/RateLimitedError (an outage, not an answer failure)
        # is never caught here -- it is not a JudgeAnswerError, so it propagates to the Warden,
        # whose Clustering rung pauses and resumes the whole Hive for an outage rather than
        # rejecting one proposal.
        return CheckResultRecord(
            kind=CheckKind.JUDGE,
            outcome=CheckOutcome.FAILED,
            reason=f"judge could not produce a verdict: {exc.detail}",
            judge_error=True,
        )
    return CheckResultRecord(
        kind=CheckKind.JUDGE,
        outcome=_OUTCOME_MAP[verdict.outcome],
        reason="; ".join(verdict.reasons) if verdict.reasons else verdict.outcome.value,
    )


# Maps the judge's own three-way verdict onto the check ladder's shared CheckOutcome vocabulary.
_OUTCOME_MAP: dict[JudgeOutcome, CheckOutcome] = {
    JudgeOutcome.APPROVE: CheckOutcome.PASSED,
    JudgeOutcome.REQUEST_CHANGES: CheckOutcome.CHANGES_REQUESTED,
    JudgeOutcome.REJECT: CheckOutcome.FAILED,
}
