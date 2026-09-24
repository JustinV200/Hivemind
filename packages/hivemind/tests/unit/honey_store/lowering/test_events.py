"""Tests for hivemind.honey_store.lowering.events: what each lowering edge records on the trail.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/lowering/events.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.lowering.events for the module under test.
"""

from __future__ import annotations

from builders.honey import make_honey_identity

from hivemind.cell import HoneyClearance
from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.lowering.events import (
    LABEL_LOWERED_KIND,
    LOWERING_PROPOSED_KIND,
    LOWERING_REJECTED_KIND,
    REJECTED_AS_INELIGIBLE,
    REJECTED_BY_APPROVER,
    applied_events,
    proposed_events,
    rejected_events,
)
from hivemind.honey_store.lowering.models import LoweringId, LoweringProposal
from hivemind.honey_store.lowering.state import LoweringState
from waggle.clock import FakeClock
from waggle.ids import new_nectar_id

_CLOCK = FakeClock()
_IDENTITY = make_honey_identity(_CLOCK)


def _proposal(**overrides: object) -> LoweringProposal:
    """A C2 -> C1 proposal with a Ripener reason, verdict reasons and a human reason set."""
    fields: dict[str, object] = {
        "id": LoweringId("lowering_01"),
        "nectar_id": new_nectar_id(_CLOCK),
        "from_label": HoneyClearance.C2,
        "to_label": HoneyClearance.C1,
        "state": LoweringState.PROPOSED,
        "ripener_reason": "Only build output.",
        "verdict_reasons": ("Nothing personal.",),
        "rubric_id": "honey-clearance/1",
        "human_reason": "Checked it myself.",
        "proposed_at": _CLOCK.now(),
    }
    fields.update(overrides)
    return LoweringProposal.model_validate(fields)


def test_proposed_events_record_both_labels_and_the_proposal_on_the_nectar() -> None:
    proposal = _proposal()

    (event,) = proposed_events(_IDENTITY, _CLOCK)(proposal)

    assert event.kind == LOWERING_PROPOSED_KIND
    assert event.subject_id == proposal.nectar_id
    assert event.payload == {"proposal_id": "lowering_01", "from": "C2", "to": "C1"}


def test_applied_events_record_a_judges_lowering_with_its_rubric() -> None:
    proposal = _proposal(state=LoweringState.LOWERED, approver=LabelApprover.JUDGE)

    (event,) = applied_events(_IDENTITY, _CLOCK)(proposal)

    assert event.kind == LABEL_LOWERED_KIND
    assert event.payload == {
        "approver": "JUDGE",
        "proposal_id": "lowering_01",
        "rubric_id": "honey-clearance/1",
        "from": "C2",
        "to": "C1",
    }


def test_applied_events_record_a_humans_lowering_without_any_rubric_or_reason() -> None:
    proposal = _proposal(state=LoweringState.LOWERED, approver=LabelApprover.HUMAN)

    (event,) = applied_events(_IDENTITY, _CLOCK)(proposal)

    assert event.payload["approver"] == "HUMAN"
    assert event.payload["rubric_id"] is None
    assert "Checked it myself." not in event.model_dump_json()


def test_applied_events_record_an_ineligible_rejection_when_the_target_no_longer_stood() -> None:
    proposal = _proposal(state=LoweringState.REJECTED, approver=LabelApprover.JUDGE)

    (event,) = applied_events(_IDENTITY, _CLOCK)(proposal)

    assert event.kind == LOWERING_REJECTED_KIND
    assert event.payload["outcome"] == REJECTED_AS_INELIGIBLE


def test_rejected_events_record_the_approver_and_the_outcome_but_never_a_reason() -> None:
    proposal = _proposal(state=LoweringState.REJECTED, approver=LabelApprover.JUDGE)

    (event,) = rejected_events(_IDENTITY, _CLOCK)(proposal)

    assert event.payload == {
        "approver": "JUDGE",
        "outcome": REJECTED_BY_APPROVER,
        "proposal_id": "lowering_01",
        "rubric_id": "honey-clearance/1",
    }
    serialised = event.model_dump_json()
    for text in ("Only build output.", "Nothing personal.", "Checked it myself."):
        assert text not in serialised


def test_rejected_events_leave_the_approver_empty_for_an_undecided_proposal() -> None:
    # Never produced by the store (a rejection is always decided), but the builder stays total.
    (event,) = rejected_events(_IDENTITY, _CLOCK)(_proposal())

    assert event.payload["approver"] is None
