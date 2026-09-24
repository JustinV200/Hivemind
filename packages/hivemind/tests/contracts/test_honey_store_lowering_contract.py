"""Contract suite for HoneyStore's label lowering (ADR-0034), over three SqliteHoneyStore harnesses.

Fits into the Hive:
    Layer 0 (test infrastructure, not shipped). Each test states one clause of the lowering half of
    hivemind.honey_store.store.protocol.HoneyStore's contract -- the labelling facts intake and
    ripening store, the candidate scan, filing, listing, noting, and the apply and reject
    transactions -- and runs against the same three harnesses as `test_honey_store_contract.py`
    (`honey_store_contract_harness.open_harness`), kept in its own module so both stay under
    codingrules 5.1's 400-line test-file limit. The `harness` fixture stays local to this module,
    as the harness module explains.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.store.protocol for the HoneyStore contract under test.
    - hivemind.honey_store.lowering.rules for lowering_target, which the candidate scan mirrors.
    - packages/hivemind/tests/contracts/honey_store_contract_harness.py for the shared harness.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from builders.honey import make_honey_draft, make_nectar_draft, make_stand_nectar_draft
from contracts.honey_store_contract_harness import HARNESS_KINDS, Harness, event, open_harness, sha

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.errors import (
    LoweringIneligibleError,
    LoweringNotFoundError,
    LoweringPostconditionError,
    LoweringTransitionError,
    NectarNotFoundError,
)
from hivemind.honey_store.lowering.models import (
    LOWERING_ID_PREFIX,
    ClearanceOutcome,
    ClearanceVerdict,
    LoweringDecision,
    LoweringFiling,
    LoweringId,
    LoweringProposal,
)
from hivemind.honey_store.lowering.rules import lowering_target
from hivemind.honey_store.lowering.state import LoweringState
from hivemind.honey_store.models import (
    HoneyPart,
    Nectar,
    NectarDraft,
    NectarOrigin,
    RipenerReading,
)
from hivemind.honey_store.store import LoweringEvents
from hivemind.pheromone import HoneyEvent, TrailQuery
from waggle.ids import new_nectar_id

_READING = RipenerReading(clearance=HoneyClearance.C0, reason="Only build output.")
_VERDICT = ClearanceVerdict(outcome=ClearanceOutcome.APPROVE, reasons=("Fine.",), rubric_id="r/1")
_NO_LONGER_ELIGIBLE = "No longer eligible"  # The start of the note an unasked rejection carries.


@pytest.fixture(params=HARNESS_KINDS)
async def harness(request: pytest.FixtureRequest, tmp_path: Path) -> Harness:
    """Build the parametrised harness: a fresh honey store over an already-trailed connection."""
    return await open_harness(request.param, tmp_path)


def _events(harness_: Harness, kind: str) -> LoweringEvents:
    """Build a LoweringEvents that records one `kind` event about the proposal's Nectar."""

    def build(proposal: LoweringProposal) -> tuple[HoneyEvent, ...]:
        """Return one event of `kind` about `proposal`'s Nectar."""
        return (event(harness_.clock, kind, proposal.nectar_id),)

    return build


def _outcome_events(harness_: Harness) -> LoweringEvents:
    """Build a LoweringEvents that names the outcome the store hands it: lowered or rejected."""

    def build(proposal: LoweringProposal) -> tuple[HoneyEvent, ...]:
        """Return `honey.label_lowered` for a LOWERED proposal, else `honey.lowering_rejected`."""
        lowered = proposal.state is LoweringState.LOWERED
        kind = "honey.label_lowered" if lowered else "honey.lowering_rejected"
        return (event(harness_.clock, kind, proposal.nectar_id),)

    return build


def _draft(harness_: Harness, **overrides: object) -> NectarDraft:
    """A Hive Stand draft declared C1 with distinct content: C2, eligible once read below C2."""
    content = f"a build log [{new_nectar_id(harness_.clock)}]".encode()
    return make_stand_nectar_draft(harness_.clock, content=content, **overrides)


async def _ripened(
    harness_: Harness,
    draft: NectarDraft,
    reading: RipenerReading | None = _READING,
    parts: int = 1,
) -> Nectar:
    """Add `draft` and ripen it into `parts` rows at its label with `reading`; return it now."""
    # A second between deposits: "oldest first" and trail order are then never a tie.
    harness_.clock.advance(1)
    added = await harness_.store.add_nectar(draft, sha(draft), lambda _: ())
    drafts = [make_honey_draft(clearance=draft.clearance)] + [
        make_honey_draft(chunk_index=index, clearance=draft.clearance, part=HoneyPart.CHUNK)
        for index in range(1, parts)
    ]
    ripened_event = event(harness_.clock, "honey.ripened", added.nectar.id)
    await harness_.store.ripen(added.nectar.id, drafts, ripened_event, reading=reading)
    return await harness_.store.get_nectar(added.nectar.id)


async def _filed(harness_: Harness, nectar: Nectar) -> LoweringProposal:
    """File `nectar`'s proposal to its rule target; return the new proposal."""
    target = lowering_target(nectar)
    assert target is not None
    harness_.clock.advance(1)
    filing = LoweringFiling(nectar_id=nectar.id, from_label=nectar.clearance, to_label=target)
    proposal = await harness_.store.add_lowering(
        filing, _events(harness_, "honey.lowering_proposed")
    )
    assert proposal is not None
    return proposal


def _decision(
    harness_: Harness, proposal: LoweringProposal, approver: LabelApprover
) -> LoweringDecision:
    """A decision on `proposal`: the judge's with a verdict, the human's with a reason."""
    is_judge = approver is LabelApprover.JUDGE
    return LoweringDecision(
        proposal_id=proposal.id,
        approver=approver,
        decided_at=harness_.clock.now(),
        verdict=_VERDICT if is_judge else None,
        human_reason=None if is_judge else "Reviewed it.",
    )


async def _apply(
    harness_: Harness, proposal: LoweringProposal, approver: LabelApprover
) -> LoweringProposal:
    """Apply `proposal` for `approver`, recording the event its outcome names."""
    harness_.clock.advance(1)
    decision = _decision(harness_, proposal, approver)
    return await harness_.store.apply_lowering(decision, _outcome_events(harness_))


async def _reject(
    harness_: Harness, proposal: LoweringProposal, approver: LabelApprover
) -> LoweringProposal:
    """Reject `proposal` for `approver`, recording one `honey.lowering_rejected` event."""
    harness_.clock.advance(1)
    events = _events(harness_, "honey.lowering_rejected")
    decision = _decision(harness_, proposal, approver)
    return await harness_.store.reject_lowering(decision, events)


async def _kinds(harness_: Harness, nectar_id: str) -> Sequence[str]:
    """Every event kind recorded about `nectar_id`, in trail order."""
    return [e.kind for e in await harness_.trail.query(TrailQuery(subject_id=nectar_id))]


# ──────────────────────────────────────────────────────────────────────────────
# The labelling facts: intake's two, ripening's reading
# ──────────────────────────────────────────────────────────────────────────────


async def test_ripen_stores_the_readers_label_without_lowering_any_row(harness: Harness) -> None:
    nectar = await _ripened(harness, _draft(harness))

    assert nectar.ripener_clearance is HoneyClearance.C0
    assert (nectar.declared_clearance, nectar.floor_clearance) == (
        HoneyClearance.C1,
        HoneyClearance.C2,
    )
    assert nectar.clearance is HoneyClearance.C2  # A reading below the label lowers nothing.
    (row,) = await harness.store.honey_for_nectar(nectar.id)
    assert row.clearance is HoneyClearance.C2


async def test_ripen_with_no_reading_stores_none(harness: Harness) -> None:
    nectar = await _ripened(harness, _draft(harness), reading=None)

    assert nectar.ripener_clearance is None


async def test_add_nectar_merge_keeps_the_higher_of_each_labelling_fact(harness: Harness) -> None:
    first = _draft(harness, declared_clearance=HoneyClearance.C0)
    added = await harness.store.add_nectar(first, sha(first), lambda _: ())
    again = first.model_copy(
        update={"declared_clearance": HoneyClearance.C1, "floor_clearance": HoneyClearance.C0}
    )

    merged = await harness.store.add_nectar(again, sha(again), lambda _: ())

    stored = await harness.store.get_nectar(added.nectar.id)
    assert (stored.declared_clearance, stored.floor_clearance) == (
        HoneyClearance.C1,
        HoneyClearance.C2,
    )
    assert merged.nectar == stored


async def test_add_nectar_merge_never_learns_a_fact_either_side_does_not_know(
    harness: Harness,
) -> None:
    unknown = make_nectar_draft(clock=harness.clock, content=b"no facts at all", bee=None)
    added = await harness.store.add_nectar(unknown, sha(unknown), lambda _: ())
    known = unknown.model_copy(
        update={"declared_clearance": HoneyClearance.C0, "floor_clearance": HoneyClearance.C0}
    )

    await harness.store.add_nectar(known, sha(known), lambda _: ())

    stored = await harness.store.get_nectar(added.nectar.id)
    assert (stored.declared_clearance, stored.floor_clearance) == (None, None)


# ──────────────────────────────────────────────────────────────────────────────
# Candidates and filing
# ──────────────────────────────────────────────────────────────────────────────


async def test_lowering_candidates_are_exactly_what_the_rule_accepts_oldest_first(
    harness: Harness,
) -> None:
    older = await _ripened(harness, _draft(harness))
    newer = await _ripened(harness, _draft(harness, declared_clearance=HoneyClearance.C0))
    refused = [
        await _ripened(harness, _draft(harness, origin=NectarOrigin.HUMAN)),
        await _ripened(harness, _draft(harness, origin=NectarOrigin.WATCH)),
        await _ripened(harness, _draft(harness, origin_tier=CombShieldLevel.NIGHT_VEIL)),
        await _ripened(harness, _draft(harness, declared_clearance=HoneyClearance.C2)),
        await _ripened(harness, _draft(harness, floor_clearance=None)),
        await _ripened(harness, _draft(harness), reading=None),
        await _ripened(harness, _draft(harness), RipenerReading(clearance=HoneyClearance.C2)),
    ]
    unripened_draft = _draft(harness)
    harness.clock.advance(1)
    unripened = await harness.store.add_nectar(unripened_draft, sha(unripened_draft), lambda _: ())
    tainted = await _ripened(harness, _draft(harness))
    harness.connection.execute("UPDATE honey_nectar SET tainted = 1 WHERE id = ?", (tainted.id,))

    candidates = await harness.store.lowering_candidates(50)

    assert [n.id for n in candidates] == [older.id, newer.id]
    everyone = [older, newer, *refused, unripened.nectar, tainted]
    for nectar in everyone:
        stored = await harness.store.get_nectar(nectar.id)
        assert (lowering_target(stored) is not None) == (stored.id in {older.id, newer.id})
    assert [n.id for n in await harness.store.lowering_candidates(1)] == [older.id]


async def test_add_lowering_files_a_proposal_once_and_takes_it_off_the_candidates(
    harness: Harness,
) -> None:
    nectar = await _ripened(harness, _draft(harness))

    proposal = await _filed(harness, nectar)

    assert proposal.id.startswith(LOWERING_ID_PREFIX)
    assert (proposal.state, proposal.from_label, proposal.to_label) == (
        LoweringState.PROPOSED,
        HoneyClearance.C2,
        HoneyClearance.C1,
    )
    assert proposal.ripener_reason == "Only build output."
    assert proposal.proposed_at == harness.clock.now()
    assert await harness.store.get_lowering(proposal.id) == proposal
    assert await harness.store.lowering_candidates(10) == ()
    filing = LoweringFiling(nectar.id, HoneyClearance.C2, HoneyClearance.C1)
    again = await harness.store.add_lowering(filing, _events(harness, "honey.lowering_proposed"))
    assert again is None
    assert await _kinds(harness, nectar.id) == ["honey.ripened", "honey.lowering_proposed"]


async def test_add_lowering_refuses_an_unknown_nectar(harness: Harness) -> None:
    filing = LoweringFiling(new_nectar_id(harness.clock), HoneyClearance.C2, HoneyClearance.C1)

    with pytest.raises(NectarNotFoundError):
        await harness.store.add_lowering(filing, _events(harness, "honey.lowering_proposed"))


async def test_list_pending_and_note_lowerings(harness: Harness) -> None:
    first = await _filed(harness, await _ripened(harness, _draft(harness)))
    second = await _filed(harness, await _ripened(harness, _draft(harness)))

    noted = await harness.store.note_lowering(first.id, "Waits for the human.", attempted=True)

    assert (noted.attempts, noted.note) == (1, "Waits for the human.")
    listed = await harness.store.list_lowerings(LoweringState.PROPOSED, 10)
    assert [p.id for p in listed] == [first.id, second.id]
    assert [p.id for p in await harness.store.list_lowerings(LoweringState.PROPOSED, 1)] == [
        first.id
    ]
    assert [p.id for p in await harness.store.pending_lowerings(10)] == [second.id]
    assert await harness.store.list_lowerings(LoweringState.LOWERED, 10) == ()
    counted = await harness.store.note_lowering(second.id, "", attempted=True)
    assert (counted.attempts, counted.note) == (1, "")


async def test_note_lowering_leaves_a_decided_proposal_alone(harness: Harness) -> None:
    proposal = await _filed(harness, await _ripened(harness, _draft(harness)))
    rejected = await _reject(harness, proposal, LabelApprover.JUDGE)

    after = await harness.store.note_lowering(proposal.id, "Too late.", attempted=True)

    assert after == rejected


async def test_lowering_reads_refuse_an_unknown_proposal(harness: Harness) -> None:
    missing = LoweringId("lowering_missing")

    with pytest.raises(LoweringNotFoundError):
        await harness.store.get_lowering(missing)
    with pytest.raises(LoweringNotFoundError):
        await harness.store.note_lowering(missing, "", attempted=True)


# ──────────────────────────────────────────────────────────────────────────────
# Applying and rejecting
# ──────────────────────────────────────────────────────────────────────────────


async def test_apply_lowering_lowers_the_nectar_and_its_rows_still_at_the_old_label(
    harness: Harness,
) -> None:
    nectar = await _ripened(harness, _draft(harness), parts=3)
    rows = await harness.store.honey_for_nectar(nectar.id)
    below = rows[1]  # The human already lowered this one row further: it keeps its own label.
    lowered_event = event(harness.clock, "honey.label_lowered", below.id)
    await harness.store.lower_clearance(below.id, HoneyClearance.C0, lowered_event)
    proposal = await _filed(harness, nectar)

    decided = await _apply(harness, proposal, LabelApprover.JUDGE)

    assert (decided.state, decided.approver) == (LoweringState.LOWERED, LabelApprover.JUDGE)
    assert (decided.verdict_reasons, decided.rubric_id) == (("Fine.",), "r/1")
    assert decided.decided_at == harness.clock.now()
    assert await harness.store.get_lowering(proposal.id) == decided
    assert (await harness.store.get_nectar(nectar.id)).clearance is HoneyClearance.C1
    labels = {row.id: row.clearance for row in await harness.store.honey_for_nectar(nectar.id)}
    assert labels[below.id] is HoneyClearance.C0
    assert sorted(label.value for label in labels.values()) == ["C0", "C1", "C1"]
    assert (await _kinds(harness, nectar.id))[-1] == "honey.label_lowered"


async def test_apply_lowering_rejects_instead_when_a_merge_raised_the_declared_label(
    harness: Harness,
) -> None:
    draft = _draft(harness)
    nectar = await _ripened(harness, draft)
    proposal = await _filed(harness, nectar)
    raised = draft.model_copy(update={"declared_clearance": HoneyClearance.C2, "bee": None})
    await harness.store.add_nectar(raised, sha(raised), lambda _: ())

    decided = await _apply(harness, proposal, LabelApprover.JUDGE)

    assert (decided.state, decided.approver) == (LoweringState.REJECTED, LabelApprover.JUDGE)
    assert decided.note.startswith(_NO_LONGER_ELIGIBLE)
    assert (await harness.store.get_nectar(nectar.id)).clearance is HoneyClearance.C2
    assert {row.clearance for row in await harness.store.honey_for_nectar(nectar.id)} == {
        HoneyClearance.C2
    }
    assert (await _kinds(harness, nectar.id))[-1] == "honey.lowering_rejected"


async def test_apply_lowering_rejects_instead_when_the_nectar_was_tainted(harness: Harness) -> None:
    nectar = await _ripened(harness, _draft(harness))
    proposal = await _filed(harness, nectar)
    harness.connection.execute("UPDATE honey_nectar SET tainted = 1 WHERE id = ?", (nectar.id,))

    decided = await _apply(harness, proposal, LabelApprover.HUMAN)

    assert decided.state is LoweringState.REJECTED
    assert decided.human_reason == "Reviewed it."


async def test_apply_lowering_rolls_everything_back_when_the_read_back_disagrees(
    harness: Harness,
) -> None:
    nectar = await _ripened(harness, _draft(harness))
    proposal = await _filed(harness, nectar)
    # SQLite silently skips the Nectar's own update: only the read-back can notice.
    harness.connection.execute(
        "CREATE TRIGGER skip_label BEFORE UPDATE OF clearance ON honey_nectar "
        "BEGIN SELECT RAISE(IGNORE); END"
    )

    with pytest.raises(LoweringPostconditionError):
        await _apply(harness, proposal, LabelApprover.JUDGE)

    assert (await harness.store.get_lowering(proposal.id)).state is LoweringState.PROPOSED
    assert {row.clearance for row in await harness.store.honey_for_nectar(nectar.id)} == {
        HoneyClearance.C2
    }
    assert "honey.label_lowered" not in await _kinds(harness, nectar.id)


async def test_apply_lowering_lets_only_the_human_lower_a_rejected_proposal(
    harness: Harness,
) -> None:
    proposal = await _filed(harness, await _ripened(harness, _draft(harness)))
    await _reject(harness, proposal, LabelApprover.JUDGE)

    with pytest.raises(LoweringTransitionError):
        await _apply(harness, proposal, LabelApprover.JUDGE)
    decided = await _apply(harness, proposal, LabelApprover.HUMAN)

    assert (decided.state, decided.approver) == (LoweringState.LOWERED, LabelApprover.HUMAN)
    assert (decided.verdict_reasons, decided.human_reason) == (("Fine.",), "Reviewed it.")
    with pytest.raises(LoweringTransitionError):
        await _apply(harness, proposal, LabelApprover.HUMAN)


async def test_apply_lowering_refuses_a_rejected_proposal_that_no_longer_stands(
    harness: Harness,
) -> None:
    nectar = await _ripened(harness, _draft(harness))
    proposal = await _filed(harness, nectar)
    rejected = await _reject(harness, proposal, LabelApprover.HUMAN)
    harness.connection.execute("UPDATE honey_nectar SET tainted = 1 WHERE id = ?", (nectar.id,))

    with pytest.raises(LoweringIneligibleError):
        await _apply(harness, proposal, LabelApprover.HUMAN)

    assert await harness.store.get_lowering(proposal.id) == rejected


async def test_reject_lowering_rejects_only_a_proposed_proposal(harness: Harness) -> None:
    nectar = await _ripened(harness, _draft(harness))
    proposal = await _filed(harness, nectar)

    rejected = await _reject(harness, proposal, LabelApprover.JUDGE)

    assert (rejected.state, rejected.approver, rejected.rubric_id) == (
        LoweringState.REJECTED,
        LabelApprover.JUDGE,
        "r/1",
    )
    assert (await _kinds(harness, nectar.id))[-1] == "honey.lowering_rejected"
    with pytest.raises(LoweringTransitionError):
        await _reject(harness, proposal, LabelApprover.HUMAN)
    missing = proposal.model_copy(update={"id": LoweringId("lowering_missing")})
    with pytest.raises(LoweringNotFoundError):
        await _reject(harness, missing, LabelApprover.HUMAN)
