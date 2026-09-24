"""Tests for hivemind.honey_store.lowering.models: a proposal, and the judge's request and verdict.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/lowering/models.py (codingrules section 3). Every pydantic
    boundary model gets a round-trip test and at least one rejection test (codingrules 14.3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.lowering.models for the module under test.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.honey_store.clearance import LabelApprover
from hivemind.honey_store.lowering.models import (
    MAX_HUMAN_REASON_CHARS,
    MAX_VERDICT_REASONS,
    ClearanceJudgeRequest,
    ClearanceOutcome,
    ClearanceVerdict,
    LoweringId,
    LoweringProposal,
)
from hivemind.honey_store.lowering.state import LoweringState
from hivemind.manifest.schema.honey import MAX_JUDGE_CHARS_CEILING
from waggle.clock import FakeClock
from waggle.ids import new_nectar_id
from waggle.messages.honey import NectarKind


def _proposal(**overrides: object) -> LoweringProposal:
    """A decided proposal with every optional field set."""
    clock = FakeClock()
    fields: dict[str, object] = {
        "id": LoweringId("lowering_01"),
        "nectar_id": new_nectar_id(clock),
        "from_label": HoneyClearance.C2,
        "to_label": HoneyClearance.C1,
        "state": LoweringState.LOWERED,
        "approver": LabelApprover.JUDGE,
        "ripener_reason": "Build output only.",
        "verdict_reasons": ("No personal detail.",),
        "rubric_id": "honey-clearance/1",
        "human_reason": None,
        "attempts": 1,
        "note": "",
        "proposed_at": clock.now(),
        "decided_at": clock.now(),
    }
    fields.update(overrides)
    return LoweringProposal.model_validate(fields)


def _request(**overrides: object) -> ClearanceJudgeRequest:
    """A well-formed judge request for a plain-text finding, target C1."""
    fields: dict[str, object] = {
        "text": "pytest passed: 42 tests in 3.1s.",
        "title": "Test run",
        "kind": NectarKind.FINDING,
        "media_type": "text/plain",
        "target": HoneyClearance.C1,
        "rubric_id": "honey-clearance/1",
    }
    fields.update(overrides)
    return ClearanceJudgeRequest.model_validate(fields)


def test_lowering_proposal_round_trips_through_json() -> None:
    proposal = _proposal()

    assert LoweringProposal.model_validate_json(proposal.model_dump_json()) == proposal


def test_lowering_proposal_defaults_describe_a_fresh_filing() -> None:
    proposal = _proposal(
        state=LoweringState.PROPOSED,
        approver=None,
        verdict_reasons=(),
        rubric_id=None,
        attempts=0,
        decided_at=None,
    )

    assert proposal.approver is None and proposal.decided_at is None
    assert proposal.note == "" and proposal.human_reason is None


@pytest.mark.parametrize(
    "bad",
    [
        {"attempts": -1},
        {"human_reason": "x" * (MAX_HUMAN_REASON_CHARS + 1)},
        {"verdict_reasons": ("r",) * (MAX_VERDICT_REASONS + 1)},
        {"state": "MAYBE"},
        {"surprise": 1},
    ],
)
def test_lowering_proposal_rejects_malformed_fields(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _proposal(**bad)


def test_clearance_judge_request_round_trips_through_json() -> None:
    request = _request()

    assert ClearanceJudgeRequest.model_validate_json(request.model_dump_json()) == request


def test_clearance_judge_request_has_no_field_for_an_id_or_the_ripeners_reason() -> None:
    # ADR-0034: the judge reviews the text, never the proposer, and shares no context with it.
    assert set(ClearanceJudgeRequest.model_fields) == {
        "text",
        "title",
        "kind",
        "media_type",
        "target",
        "rubric_id",
    }
    with pytest.raises(ValidationError):
        _request(ripener_reason="It said C0.")


@pytest.mark.parametrize(
    "bad",
    [{"text": ""}, {"text": "x" * (MAX_JUDGE_CHARS_CEILING + 1)}, {"rubric_id": ""}],
)
def test_clearance_judge_request_rejects_an_empty_or_oversized_field(
    bad: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        _request(**bad)


def test_clearance_verdict_round_trips_through_json() -> None:
    verdict = ClearanceVerdict(
        outcome=ClearanceOutcome.REJECT, reasons=("A first name in line 2.",), rubric_id="r/1"
    )

    assert ClearanceVerdict.model_validate_json(verdict.model_dump_json()) == verdict


@pytest.mark.parametrize(
    "bad",
    [
        {"outcome": "MAYBE", "rubric_id": "r/1"},
        {"outcome": "APPROVE"},
        {"outcome": "APPROVE", "rubric_id": "r/1", "reasons": ["r"] * 9},
    ],
)
def test_clearance_verdict_rejects_a_malformed_answer(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ClearanceVerdict.model_validate(bad)
