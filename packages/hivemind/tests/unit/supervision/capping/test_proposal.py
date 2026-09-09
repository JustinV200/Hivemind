"""Unit tests for hivemind.supervision.capping.proposal: Proposal construction and round trip."""

from __future__ import annotations

import pytest
from builders.capping import make_proposal
from pydantic import ValidationError

from hivemind.supervision.capping.proposal import Proposal
from hivemind.supervision.capping.state import ProposalState
from hivemind.supervision.capping.tiers import RiskTier


def test_make_proposal_defaults_to_proposed_state() -> None:
    proposal = make_proposal()

    assert proposal.state is ProposalState.PROPOSED
    assert proposal.risk_tier is RiskTier.SCRATCH_WRITE


def test_proposal_round_trips_through_json() -> None:
    proposal = make_proposal()

    restored = Proposal.model_validate_json(proposal.model_dump_json())

    assert restored == proposal


def test_proposal_is_frozen() -> None:
    proposal = make_proposal()

    with pytest.raises(ValidationError, match="frozen"):
        proposal.reason = "changed"  # type: ignore[misc]  # The assignment is the test.


def test_proposal_rejects_an_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Proposal.model_validate({**make_proposal().model_dump(), "extra": "nope"})


def test_proposal_state_changes_via_model_copy_leave_the_original_unchanged() -> None:
    proposal = make_proposal()

    checking = proposal.model_copy(update={"state": ProposalState.CHECKING})

    assert proposal.state is ProposalState.PROPOSED
    assert checking.state is ProposalState.CHECKING
    assert checking.id == proposal.id
