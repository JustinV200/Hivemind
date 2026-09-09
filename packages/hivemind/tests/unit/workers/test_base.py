"""Unit tests for hivemind.workers.base: WorkerOutcome's claimed-xor-handoff validation."""

from __future__ import annotations

import pytest
from builders.memory import make_handoff
from builders.workers import make_outcome
from pydantic import ValidationError

from hivemind.cell import HoneyClearance
from hivemind.workers.base import WorkerOutcome


def test_make_outcome_default_is_claimed_with_no_handoff() -> None:
    outcome = make_outcome()

    assert outcome.claimed is True
    assert outcome.handoff is None


def test_outcome_may_carry_a_handoff_instead_of_claiming() -> None:
    handoff = make_handoff()

    outcome = make_outcome(claimed=False, handoff=handoff)

    assert outcome.claimed is False
    assert outcome.handoff is handoff


def test_claimed_true_with_a_handoff_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_outcome(claimed=True, handoff=make_handoff())


def test_claimed_false_with_no_handoff_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_outcome(claimed=False, handoff=None)


def test_outcome_is_frozen() -> None:
    outcome = make_outcome()

    with pytest.raises(ValidationError):
        outcome.summary = "changed"  # type: ignore[misc]


def test_outcome_rejects_negative_spend() -> None:
    with pytest.raises(ValidationError):
        make_outcome(spend_usd=-0.01)


def test_outcome_carries_its_clearance_and_artifacts() -> None:
    outcome = make_outcome(clearance=HoneyClearance.C2, artifacts=())

    assert outcome.clearance is HoneyClearance.C2
    assert outcome.artifacts == ()


def test_worker_outcome_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        WorkerOutcome(
            summary="x",
            clearance=HoneyClearance.C1,
            claimed=True,
            spend_usd=0.0,
            unexpected="nope",  # type: ignore[call-arg]
        )
