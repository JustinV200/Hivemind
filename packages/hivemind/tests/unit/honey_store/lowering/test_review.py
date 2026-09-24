"""Tests for hivemind.honey_store.lowering.review: LabelLowering over a real SQLite Honey Store.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/lowering/review.py (codingrules section 3). Every judge is a
    FakeClearanceJudge (codingrules 14.4, fakes over mocks); the store and the trail are real.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.lowering.review for the module under test.
    - docs/adr/0034-honey-label-lowering-is-a-judge-reviewed-proposal.md for the flow.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.honey import (
    make_lowering_deps,
    make_stand_nectar_draft,
    open_test_honey_store_with_trail,
    store_ripened,
)

from hivemind.cell import HoneyClearance
from hivemind.common.sqlite import connect
from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.errors import (
    LoweringIneligibleError,
    LoweringInputError,
    LoweringNotFoundError,
    LoweringTransitionError,
)
from hivemind.honey_store.lowering.fake import FakeClearanceJudge
from hivemind.honey_store.lowering.judge import RUBRIC_ID, ClearanceJudge
from hivemind.honey_store.lowering.models import (
    MAX_HUMAN_REASON_CHARS,
    ClearanceJudgeRequest,
    ClearanceOutcome,
    ClearanceVerdict,
    LoweringId,
    LoweringProposal,
)
from hivemind.honey_store.lowering.review import (
    NO_TEXT_NOTE,
    UNANSWERED_NOTE,
    LabelLowering,
    ReviewOutcome,
)
from hivemind.honey_store.lowering.state import LoweringState
from hivemind.honey_store.models import Nectar, NectarOrigin, RipenerReading
from hivemind.honey_store.store.sqlite import SqliteHoneyStore
from hivemind.llm import ProviderUnavailableError
from hivemind.manifest import HoneyLoweringSection
from hivemind.pheromone import PheromoneEvent, SqlitePheromoneTrail, TrailQuery
from waggle.clock import FakeClock
from waggle.ids import new_nectar_id

_APPROVE = ClearanceOutcome.APPROVE
_REJECT = ClearanceOutcome.REJECT
_READING = RipenerReading(clearance=HoneyClearance.C0, reason="Only build output.")


@dataclass(frozen=True, slots=True)
class _Hive:
    """A real store and trail on one file, and the shared clock."""

    store: SqliteHoneyStore
    trail: SqlitePheromoneTrail
    clock: FakeClock

    def lowering(self, judge: ClearanceJudge | None = None, **settings: object) -> LabelLowering:
        """Build the flow under test with `judge` and `[honey.lowering]` overrides."""
        section = HoneyLoweringSection.model_validate(settings)
        return LabelLowering(
            make_lowering_deps(self.store, self.clock, judge=judge, settings=section)
        )

    async def eligible(self, text: str = "pytest passed: 42 tests.", **overrides: object) -> Nectar:
        """Store and ripen one Hive Stand deposit declared C1 and read C0: eligible, target C1."""
        content = f"{text} [{new_nectar_id(self.clock)}]".encode()
        draft = make_stand_nectar_draft(self.clock, content=content, **overrides)
        return await store_ripened(self.store, self.clock, draft, _READING)

    async def filed(self, text: str = "pytest passed: 42 tests.") -> LoweringProposal:
        """Store one eligible Nectar and file its proposal; return the proposal."""
        await self.eligible(text)
        assert await self.lowering().file_proposals() == 1
        (proposal,) = await self.store.list_lowerings(LoweringState.PROPOSED, 10)
        return proposal

    async def events(self, kind: str) -> tuple[PheromoneEvent, ...]:
        """Return every event of `kind` on the trail."""
        return await self.trail.query(TrailQuery(kind=kind))


@pytest.fixture
async def hive(tmp_path: Path) -> _Hive:
    """A fresh store and trail."""
    clock = FakeClock()
    store, trail = await open_test_honey_store_with_trail(tmp_path, clock)
    return _Hive(store=store, trail=trail, clock=clock)


# ──────────────────────────────────────────────────────────────────────────────
# Filing
# ──────────────────────────────────────────────────────────────────────────────


async def test_file_proposals_files_an_eligible_nectar_with_its_ripeners_reason(
    hive: _Hive,
) -> None:
    nectar = await hive.eligible()

    filed = await hive.lowering().file_proposals()

    assert filed == 1
    (proposal,) = await hive.store.list_lowerings(LoweringState.PROPOSED, 10)
    assert (proposal.nectar_id, proposal.from_label, proposal.to_label) == (
        nectar.id,
        HoneyClearance.C2,
        HoneyClearance.C1,
    )
    assert proposal.ripener_reason == "Only build output."
    (event,) = await hive.events("honey.lowering_proposed")
    assert event.payload == {"proposal_id": proposal.id, "from": "C2", "to": "C1"}


async def test_file_proposals_passes_over_nectar_the_rule_refuses(hive: _Hive) -> None:
    await hive.eligible(origin=NectarOrigin.HUMAN)
    await hive.eligible(declared_clearance=None)
    heuristic = make_stand_nectar_draft(hive.clock, content=b"A short note, read by no model.")
    await store_ripened(hive.store, hive.clock, heuristic, None)

    assert await hive.lowering().file_proposals() == 0


async def test_file_proposals_files_once_per_nectar_ever(hive: _Hive) -> None:
    await hive.eligible()
    lowering = hive.lowering()

    first, second = await lowering.file_proposals(), await lowering.file_proposals()

    assert (first, second) == (1, 0)
    assert len(await hive.events("honey.lowering_proposed")) == 1


async def test_file_proposals_trusts_the_rule_over_the_scan_and_survives_a_filing_race(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    connection = connect(tmp_path / "hive.sqlite3")
    trail = await SqlitePheromoneTrail.create(connection, clock)
    store = await _StaleScanStore.create(connection, clock)
    assert isinstance(store, _StaleScanStore)  # `create` builds the subclass it is called on.
    hive = _Hive(store=store, trail=trail, clock=clock)
    refused = await hive.eligible(declared_clearance=None)
    raced = await hive.eligible()
    await hive.lowering().file_proposals()
    # A scan that (wrongly) offers a row the rule refuses, and a row another runner just filed.
    store.stale = (refused, raced)

    filed = await hive.lowering().file_proposals()

    assert filed == 0
    assert len(await store.list_lowerings(LoweringState.PROPOSED, 10)) == 1


async def test_file_proposals_files_at_most_max_proposals_per_pass(hive: _Hive) -> None:
    for index in range(3):
        await hive.eligible(f"finding {index}")
    lowering = hive.lowering(max_proposals_per_pass=2)

    assert (await lowering.file_proposals(), await lowering.file_proposals()) == (2, 1)


# ──────────────────────────────────────────────────────────────────────────────
# The judge's review
# ──────────────────────────────────────────────────────────────────────────────


async def test_review_pending_without_a_judge_or_when_disabled_leaves_proposals_waiting(
    hive: _Hive,
) -> None:
    proposal = await hive.filed()
    judge = FakeClearanceJudge(default=_APPROVE)

    assert await hive.lowering().review_pending() == ReviewOutcome()
    assert await hive.lowering(judge, enabled=False).review_pending() == ReviewOutcome()

    assert judge.requests == []
    assert (await hive.store.get_lowering(proposal.id)).state is LoweringState.PROPOSED


async def test_review_pending_lowers_an_approved_nectar_and_its_rows(hive: _Hive) -> None:
    proposal = await hive.filed()

    outcome = await hive.lowering(FakeClearanceJudge(_APPROVE)).review_pending()

    assert outcome == ReviewOutcome(lowered=1)
    decided = await hive.store.get_lowering(proposal.id)
    assert (decided.state, decided.approver) == (LoweringState.LOWERED, LabelApprover.JUDGE)
    assert decided.rubric_id == RUBRIC_ID and decided.verdict_reasons
    assert (await hive.store.get_nectar(proposal.nectar_id)).clearance is HoneyClearance.C1
    rows = await hive.store.honey_for_nectar(proposal.nectar_id)
    assert {row.clearance for row in rows} == {HoneyClearance.C1}
    (event,) = await hive.events("honey.label_lowered")
    assert event.payload["approver"] == "JUDGE" and event.payload["rubric_id"] == RUBRIC_ID


async def test_review_pending_rejects_what_the_judge_rejects(hive: _Hive) -> None:
    proposal = await hive.filed()

    outcome = await hive.lowering(FakeClearanceJudge(_REJECT)).review_pending()

    assert outcome == ReviewOutcome(rejected=1)
    assert (await hive.store.get_lowering(proposal.id)).state is LoweringState.REJECTED
    assert (await hive.store.get_nectar(proposal.nectar_id)).clearance is HoneyClearance.C2
    (event,) = await hive.events("honey.lowering_rejected")
    assert (event.payload["approver"], event.payload["outcome"]) == ("JUDGE", "REJECT")


async def test_review_pending_shows_the_judge_the_text_and_target_but_no_id_or_reason(
    hive: _Hive,
) -> None:
    proposal = await hive.filed("The nightly build is green.")
    judge = FakeClearanceJudge(_REJECT)

    await hive.lowering(judge).review_pending()

    (request,) = judge.requests
    assert request.text.startswith("The nightly build is green.")
    assert (request.title, request.target, request.rubric_id) == (
        "A test finding",
        HoneyClearance.C1,
        RUBRIC_ID,
    )
    shown = request.model_dump_json()
    assert proposal.nectar_id not in shown and proposal.id not in shown
    assert "Only build output." not in shown


async def test_review_pending_counts_answer_failures_then_hands_the_proposal_to_the_human(
    hive: _Hive,
) -> None:
    proposal = await hive.filed()
    judge = FakeClearanceJudge()  # Cannot answer at all: its script is empty.
    lowering = hive.lowering(judge, max_attempts=2)

    first, second, third = [await lowering.review_pending() for _ in range(3)]

    assert first == ReviewOutcome(unanswered=1)
    assert second == ReviewOutcome(unanswered=1, handed_to_human=1)
    assert third == ReviewOutcome()  # Noted: the judge's queue passes it over.
    assert len(judge.requests) == 2
    waiting = await hive.store.get_lowering(proposal.id)
    assert (waiting.state, waiting.attempts) == (LoweringState.PROPOSED, 2)
    assert waiting.note == UNANSWERED_NOTE.format(attempts=2)


async def test_review_pending_never_sends_a_text_over_max_judge_chars(hive: _Hive) -> None:
    proposal = await hive.filed("x" * 300)
    judge = FakeClearanceJudge(default=_APPROVE)

    outcome = await hive.lowering(judge, max_judge_chars=100).review_pending()

    assert outcome == ReviewOutcome(handed_to_human=1)
    assert judge.requests == []
    waiting = await hive.store.get_lowering(proposal.id)
    assert "over [honey.lowering] max_judge_chars (100)" in waiting.note
    assert waiting.attempts == 0


async def test_review_pending_hands_a_deposit_with_no_readable_text_to_the_human(
    hive: _Hive,
) -> None:
    await hive.eligible(media_type="application/octet-stream")
    await hive.lowering().file_proposals()
    judge = FakeClearanceJudge(default=_APPROVE)

    outcome = await hive.lowering(judge).review_pending()

    assert outcome == ReviewOutcome(handed_to_human=1)
    (waiting,) = await hive.store.list_lowerings(LoweringState.PROPOSED, 10)
    assert waiting.note == NO_TEXT_NOTE


async def test_review_pending_rejects_an_approval_whose_target_no_longer_stands(
    hive: _Hive,
) -> None:
    proposal = await hive.filed("A merged finding.")
    # A second depositor declares the same content C2: the label is no longer the floor's alone.
    nectar = await hive.store.get_nectar(proposal.nectar_id)
    content = await hive.store.nectar_content(nectar.id)
    raise_it = make_stand_nectar_draft(
        hive.clock, content=content, declared_clearance=HoneyClearance.C2
    )
    await hive.store.add_nectar(raise_it, hashlib.sha256(content).hexdigest(), lambda _: ())

    outcome = await hive.lowering(FakeClearanceJudge(_APPROVE)).review_pending()

    assert outcome == ReviewOutcome(rejected=1)
    assert (await hive.store.get_nectar(nectar.id)).clearance is HoneyClearance.C2
    (event,) = await hive.events("honey.lowering_rejected")
    assert event.payload["outcome"] == "INELIGIBLE"


async def test_review_pending_reviews_at_most_max_reviews_per_pass(hive: _Hive) -> None:
    for index in range(3):
        await hive.eligible(f"finding {index}")
    await hive.lowering().file_proposals()

    lowering = hive.lowering(FakeClearanceJudge(default=_REJECT), max_reviews_per_pass=2)

    first, second = await lowering.review_pending(), await lowering.review_pending()

    assert (first, second) == (ReviewOutcome(rejected=2), ReviewOutcome(rejected=1))


async def test_review_pending_lets_a_judge_outage_through(hive: _Hive) -> None:
    await hive.filed()

    with pytest.raises(ProviderUnavailableError):
        await hive.lowering(_DownJudge()).review_pending()


async def test_review_pending_skips_a_proposal_the_human_decided_mid_review(hive: _Hive) -> None:
    proposal = await hive.filed()
    judge = _HumanFirstJudge(hive.lowering(), proposal.id)

    outcome = await hive.lowering(judge).review_pending()

    assert outcome == ReviewOutcome()  # The judge's approval found it REJECTED: skipped.
    decided = await hive.store.get_lowering(proposal.id)
    assert (decided.state, decided.approver) == (LoweringState.REJECTED, LabelApprover.HUMAN)


# ──────────────────────────────────────────────────────────────────────────────
# The human's decision
# ──────────────────────────────────────────────────────────────────────────────


async def test_decide_lowers_as_the_human_and_keeps_the_reason_off_the_trail(hive: _Hive) -> None:
    proposal = await hive.filed()

    decided = await hive.lowering().decide(proposal.id, True, "  Reviewed: build output only.  ")

    assert (decided.state, decided.approver) == (LoweringState.LOWERED, LabelApprover.HUMAN)
    assert decided.human_reason == "Reviewed: build output only."
    assert (await hive.store.get_lowering(proposal.id)).human_reason == decided.human_reason
    (event,) = await hive.events("honey.label_lowered")
    assert event.payload["approver"] == "HUMAN" and event.payload["rubric_id"] is None
    assert "Reviewed" not in event.model_dump_json()


async def test_decide_lowers_a_proposal_the_judge_rejected(hive: _Hive) -> None:
    proposal = await hive.filed()
    await hive.lowering(FakeClearanceJudge(_REJECT)).review_pending()

    decided = await hive.lowering().decide(proposal.id, True, "The judge was too strict.")

    assert decided.state is LoweringState.LOWERED
    assert decided.rubric_id == RUBRIC_ID and decided.verdict_reasons  # The judge's word stays.


async def test_decide_denies_a_waiting_proposal(hive: _Hive) -> None:
    proposal = await hive.filed()

    decided = await hive.lowering().decide(proposal.id, False, "Keep it C2.")

    assert (decided.state, decided.approver) == (LoweringState.REJECTED, LabelApprover.HUMAN)
    (event,) = await hive.events("honey.lowering_rejected")
    assert (event.payload["approver"], event.payload["outcome"]) == ("HUMAN", "REJECT")


async def test_decide_refuses_edges_the_table_forbids(hive: _Hive) -> None:
    proposal = await hive.filed()
    lowering = hive.lowering()
    await lowering.decide(proposal.id, False, "No.")

    with pytest.raises(LoweringTransitionError):
        await lowering.decide(proposal.id, False, "Still no.")
    await lowering.decide(proposal.id, True, "Changed my mind.")
    with pytest.raises(LoweringTransitionError):
        await lowering.decide(proposal.id, True, "Again.")


async def test_decide_refuses_an_approval_that_no_longer_stands_on_a_rejected_proposal(
    hive: _Hive,
) -> None:
    proposal = await hive.filed()
    lowering = hive.lowering()
    await lowering.decide(proposal.id, False, "No.")
    content = await hive.store.nectar_content(proposal.nectar_id)
    raise_it = make_stand_nectar_draft(
        hive.clock, content=content, declared_clearance=HoneyClearance.C2
    )
    await hive.store.add_nectar(raise_it, hashlib.sha256(content).hexdigest(), lambda _: ())

    with pytest.raises(LoweringIneligibleError):
        await lowering.decide(proposal.id, True, "Lower it after all.")


@pytest.mark.parametrize("reason", ["", "   ", "x" * (MAX_HUMAN_REASON_CHARS + 1)])
async def test_decide_requires_a_bounded_reason(hive: _Hive, reason: str) -> None:
    proposal = await hive.filed()

    with pytest.raises(LoweringInputError):
        await hive.lowering().decide(proposal.id, True, reason)

    assert (await hive.store.get_lowering(proposal.id)).state is LoweringState.PROPOSED


async def test_decide_refuses_an_unknown_proposal(hive: _Hive) -> None:
    with pytest.raises(LoweringNotFoundError):
        await hive.lowering().decide(LoweringId("lowering_missing"), True, "Why not.")


def test_review_outcome_sums_field_by_field() -> None:
    total = ReviewOutcome(lowered=1, unanswered=2) + ReviewOutcome(rejected=3, handed_to_human=4)

    assert total == ReviewOutcome(lowered=1, rejected=3, unanswered=2, handed_to_human=4)


class _StaleScanStore(SqliteHoneyStore):
    """A real store whose candidate scan answers `stale` once it is set, not the table."""

    stale: tuple[Nectar, ...] | None = None

    async def lowering_candidates(self, limit: int) -> tuple[Nectar, ...]:
        """Return the stale candidates when set; the real scan otherwise."""
        if self.stale is None:
            return await super().lowering_candidates(limit)
        return self.stale[:limit]


class _DownJudge:
    """A ClearanceJudge whose provider, and every fallback, is down."""

    async def judge(self, request: ClearanceJudgeRequest) -> ClearanceVerdict:
        """Raise the outage a real JUDGE binding raises."""
        raise ProviderUnavailableError("judge", "down for the test")


class _HumanFirstJudge:
    """A ClearanceJudge whose answer arrives only after the human denied the same proposal."""

    def __init__(self, human: LabelLowering, proposal_id: LoweringId) -> None:
        """Remember who denies what before this judge approves."""
        self._human = human
        self._proposal_id = proposal_id

    async def judge(self, request: ClearanceJudgeRequest) -> ClearanceVerdict:
        """Let the human deny first, then approve: the store must refuse the stale approval."""
        await self._human.decide(self._proposal_id, False, "Denied while the judge thought.")
        return ClearanceVerdict(outcome=_APPROVE, reasons=(), rubric_id=request.rubric_id)
