"""Run judge-reviewed label lowering: file proposals, ask the judge, and take the human's decision.

Anything gathered on a Real Cell (a borrowed device, the Hive Stand included) is labelled C2 in the
Honey Store (the Hive's knowledge base) by the provenance floor, whatever it says, so a default C1
goal never reads what earlier runs learned there. ADR-0034 lets that label come down when an
independent judge agrees. `LabelLowering` is the whole flow over one store: `file_proposals` files
one proposal per newly eligible Nectar (a raw deposit, with the Honey rows ripened from it);
`review_pending` asks the judge (the JUDGE model slot, through a `ClearanceJudge`) about proposals
still waiting, and applies or rejects each one; `decide` is the human's approval or denial. The
Ripener proposes, it never approves; the judge sees only the deposit's text and the target; the
write applies and verifies in one transaction; and every edge is on the trail.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.honey_store.lowering`.
    Driven by the House Bee's ripening pass beside the Queen (after it ripens, never inside her
    tick) and by `hive honey review`. Calls into the store, this package's rule, judge seam,
    models and event builders, ripening's own text decoder, and `hivemind.manifest`
    (`[honey.lowering]`) only.

Key invariants:
    - A label goes down only through `HoneyStore.apply_lowering`, whose transaction re-checks
      eligibility; nothing here ever lowers a label directly.
    - The judge is shown the deposit's text only when it is at most `max_judge_chars` long, whole;
      a longer or unreadable one waits for the human, with a note saying why.
    - A judge that cannot answer counts an attempt and leaves the proposal PROPOSED; after
      `max_attempts` a note hands it to the human. An outage (`ProviderUnavailableError`,
      `RateLimitedError`) propagates untouched: Clustering handles it.
    - The human's reason is required and bounded, stored on the proposal, never on the trail.

See Also:
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the decision.
    - hivemind.honey_store.lowering.rules for the eligibility rule.
    - hivemind.honey_store.store.protocol for the lowering methods this drives.
"""

from __future__ import annotations

from dataclasses import dataclass

from hivemind.common.errors import NotFoundError
from hivemind.common.logging import get_logger
from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.errors import (
    ClearanceJudgeAnswerError,
    LoweringInputError,
    LoweringTransitionError,
)
from hivemind.honey_store.identity import HoneyIdentity
from hivemind.honey_store.lowering.events import applied_events, proposed_events, rejected_events
from hivemind.honey_store.lowering.judge import RUBRIC_ID, ClearanceJudge
from hivemind.honey_store.lowering.models import (
    MAX_HUMAN_REASON_CHARS,
    ClearanceJudgeRequest,
    ClearanceOutcome,
    ClearanceVerdict,
    LoweringDecision,
    LoweringFiling,
    LoweringId,
    LoweringProposal,
)
from hivemind.honey_store.lowering.rules import lowering_target
from hivemind.honey_store.lowering.state import LoweringState
from hivemind.honey_store.models import Nectar
from hivemind.honey_store.ripening import decode_text, normalise_text
from hivemind.honey_store.store import HoneyStore
from hivemind.manifest import HoneyLoweringSection
from waggle.clock import Clock

NO_TEXT_NOTE = "It has no text a judge can read, so it waits for the human."
TOO_LONG_NOTE = (  # Filled with the text's length and the bound it is over.
    "Its text is {chars} characters, over [honey.lowering] max_judge_chars ({limit}); a judge "
    "must see everything it clears, so it waits for the human."
)
UNANSWERED_NOTE = "The judge could not answer {attempts} times, so it waits for the human."

__all__ = [
    "NO_TEXT_NOTE",
    "TOO_LONG_NOTE",
    "UNANSWERED_NOTE",
    "LabelLowering",
    "LoweringDeps",
    "ReviewOutcome",
]

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LoweringDeps:
    """The store, identity, clock, settings and judge one `LabelLowering` runs on.

    Attributes:
        store: Where candidates are read and proposals, labels and events are written.
        identity: The Hive, node and actor every event is stamped with: "system" for the House
            Bee, "human" for `hive honey review`'s own decisions.
        clock: The injected time source for every event and decision.
        settings: `[honey.lowering]`: whether the judge reviews, its text bound, pass sizes and
            the attempts a judge gets.
        judge: The clearance judge; None (no JUDGE binding) leaves every proposal to the human.
    """

    store: HoneyStore
    identity: HoneyIdentity
    clock: Clock
    settings: HoneyLoweringSection
    judge: ClearanceJudge | None = None


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    """What one `review_pending` call did, in counts; sums with `+` across proposals."""

    lowered: int = 0  # Proposals the judge approved and the store lowered.
    rejected: int = 0  # Rejected by the judge, or no longer eligible when applied.
    unanswered: int = 0  # Judge answer failures (an attempt counted; still PROPOSED).
    handed_to_human: int = 0  # Newly noted as waiting for the human (too long, or out of attempts).

    def __add__(self, other: ReviewOutcome) -> ReviewOutcome:
        """Return the field-by-field sum of two outcomes."""
        return ReviewOutcome(
            lowered=self.lowered + other.lowered,
            rejected=self.rejected + other.rejected,
            unanswered=self.unanswered + other.unanswered,
            handed_to_human=self.handed_to_human + other.handed_to_human,
        )


class LabelLowering:
    """File, review and decide label lowering proposals over one Honey Store (ADR-0034).

    Holds no state between calls: every proposal, attempt and note lives in the store, so the House
    Bee's pass and `hive honey review` may each run their own instance on the same file.
    """

    def __init__(self, deps: LoweringDeps) -> None:
        """Wire the flow to its store, identity, clock, settings and judge.

        Args:
            deps: Everything it runs on; see LoweringDeps.
        """
        self._deps = deps

    async def file_proposals(self) -> int:
        """File one proposal for each newly eligible Nectar, up to `max_proposals_per_pass`.

        Returns:
            How many proposals were filed, each with its `honey.lowering_proposed` event.
        """
        deps = self._deps
        # Local SQLite, bounded by the connection's busy timeout; oldest first.
        candidates = await deps.store.lowering_candidates(deps.settings.max_proposals_per_pass)
        events = proposed_events(deps.identity, deps.clock)
        filed = 0
        # The pure rule is the authority; the store's scan only mirrors it to keep a pass bounded.
        for nectar in candidates:
            target = lowering_target(nectar)
            if target is None:
                continue
            filing = LoweringFiling(
                nectar_id=nectar.id, from_label=nectar.clearance, to_label=target
            )
            # None: another runner filed this Nectar first (one proposal per Nectar, ever).
            if await deps.store.add_lowering(filing, events) is not None:
                filed += 1
        return filed

    async def review_pending(self) -> ReviewOutcome:
        """Ask the judge about proposals still waiting for it, up to `max_reviews_per_pass`.

        Returns:
            How many were lowered, rejected, left unanswered, or handed to the human.

        Raises:
            ProviderUnavailableError: The judge's provider (and every fallback) is down; an
                outage for Clustering, never one proposal's failure.
            RateLimitedError: The same, for a rate limit.
        """
        deps = self._deps
        judge = deps.judge
        # No JUDGE binding, or the operator turned review off: every proposal waits for the human.
        if judge is None or not deps.settings.enabled:
            return ReviewOutcome()
        # Local SQLite, bounded by the connection's busy timeout; oldest first, noted ones skipped.
        pending = await deps.store.pending_lowerings(deps.settings.max_reviews_per_pass)
        outcome = ReviewOutcome()
        # One proposal at a time: one decided or gone meanwhile never stops the rest.
        for proposal in pending:
            try:
                outcome = outcome + await self._review_one(judge, proposal)
            except (LoweringTransitionError, NotFoundError) as error:
                log.warning(
                    "honey_store.lowering_review_skipped", proposal_id=proposal.id, error=error.code
                )
        return outcome

    async def decide(self, proposal_id: LoweringId, approve: bool, reason: str) -> LoweringProposal:
        """Approve or deny one proposal as the human (the last word, codingrules 8.8).

        Args:
            proposal_id: The proposal to decide.
            approve: True lowers it (from PROPOSED, or REJECTED); False denies it (from PROPOSED).
            reason: Why; required, at most `MAX_HUMAN_REASON_CHARS`, stored on the proposal and
                never put on the trail.

        Returns:
            The proposal as decided: LOWERED, or REJECTED (a denial, or an approval whose target
            no longer stood).

        Raises:
            LoweringInputError: The reason is empty or too long; nothing was read or written.
            LoweringNotFoundError: No proposal has `proposal_id`.
            LoweringTransitionError: The table has no such edge (a denial of a REJECTED one,
                anything on a LOWERED one).
            LoweringIneligibleError: An approval of a REJECTED proposal that no longer stands.
        """
        deps = self._deps
        decision = LoweringDecision(
            proposal_id=proposal_id,
            approver=LabelApprover.HUMAN,
            decided_at=deps.clock.now(),
            human_reason=_checked_reason(reason),
        )
        # Local SQLite, one transaction for the decision, any label change and its event.
        if approve:
            return await deps.store.apply_lowering(
                decision, applied_events(deps.identity, deps.clock)
            )
        return await deps.store.reject_lowering(
            decision, rejected_events(deps.identity, deps.clock)
        )

    async def _review_one(self, judge: ClearanceJudge, proposal: LoweringProposal) -> ReviewOutcome:
        """Show one proposal's text to the judge and act on its verdict, or hand it to the human."""
        deps = self._deps
        # Local SQLite reads, bounded by the connection's busy timeout.
        nectar = await deps.store.get_nectar(proposal.nectar_id)
        text = _judged_text(await deps.store.nectar_content(nectar.id), nectar.media_type)
        note = _unjudgeable_note(text, deps.settings.max_judge_chars)
        # A text the judge cannot see whole is never sent at all: it waits for the human.
        if note:
            await deps.store.note_lowering(proposal.id, note, attempted=False)
            return ReviewOutcome(handed_to_human=1)
        try:
            # External await: one JUDGE call under the judge's own timeout (seconds to minutes).
            verdict = await judge.judge(_request(nectar, text, proposal))
        except ClearanceJudgeAnswerError as error:
            return await self._count_failure(proposal, error)
        return await self._act_on(proposal, verdict)

    async def _count_failure(
        self, proposal: LoweringProposal, error: ClearanceJudgeAnswerError
    ) -> ReviewOutcome:
        """Count one answer failure; after `max_attempts`, note that it waits for the human."""
        deps = self._deps
        attempts = proposal.attempts + 1
        exhausted = attempts >= deps.settings.max_attempts
        log.warning(
            "honey_store.clearance_judge_unanswered",
            proposal_id=proposal.id,
            cause=error.cause,
            attempts=attempts,
            handed_to_human=exhausted,
        )
        note = UNANSWERED_NOTE.format(attempts=attempts) if exhausted else ""
        # Local SQLite, bounded by the connection's busy timeout.
        await deps.store.note_lowering(proposal.id, note, attempted=True)
        return ReviewOutcome(unanswered=1, handed_to_human=1 if exhausted else 0)

    async def _act_on(self, proposal: LoweringProposal, verdict: ClearanceVerdict) -> ReviewOutcome:
        """Apply an APPROVE (the store may still reject it as ineligible) or record a REJECT."""
        deps = self._deps
        decision = LoweringDecision(
            proposal_id=proposal.id,
            approver=LabelApprover.JUDGE,
            decided_at=deps.clock.now(),
            verdict=verdict,
        )
        # Local SQLite, one transaction for the decision, any label change and its event.
        if verdict.outcome is ClearanceOutcome.APPROVE:
            decided = await deps.store.apply_lowering(
                decision, applied_events(deps.identity, deps.clock)
            )
            lowered = decided.state is LoweringState.LOWERED
            return ReviewOutcome(lowered=1) if lowered else ReviewOutcome(rejected=1)
        await deps.store.reject_lowering(decision, rejected_events(deps.identity, deps.clock))
        return ReviewOutcome(rejected=1)


def _request(nectar: Nectar, text: str, proposal: LoweringProposal) -> ClearanceJudgeRequest:
    """Build what the judge is shown: the deposit and the target, never an id or the reason."""
    return ClearanceJudgeRequest(
        text=text,
        title=nectar.title,
        kind=nectar.kind,
        media_type=nectar.media_type,
        target=proposal.to_label,
        rubric_id=RUBRIC_ID,
    )


def _judged_text(content: bytes, media_type: str) -> str:
    """Decode a deposit's bytes exactly as ripening does; empty when there is no text to judge."""
    decoded = decode_text(content, media_type)
    return normalise_text(decoded) if decoded is not None else ""


def _unjudgeable_note(text: str, max_chars: int) -> str:
    """Say why the judge may not see `text` (unreadable, or too long to see whole); else ""."""
    if not text:
        return NO_TEXT_NOTE
    if len(text) > max_chars:
        return TOO_LONG_NOTE.format(chars=len(text), limit=max_chars)
    return ""


def _checked_reason(reason: str) -> str:
    """Return the human's stripped reason, refusing an empty or overlong one: it must say why."""
    stripped = reason.strip()
    if not stripped:
        raise LoweringInputError("a decision on a lowering proposal must say why")
    if len(stripped) > MAX_HUMAN_REASON_CHARS:
        raise LoweringInputError(f"it is over {MAX_HUMAN_REASON_CHARS} characters")
    return stripped
