"""Tests for hivemind.wardens.nurse.accept and .trace: the accept path's side of the Nurse Bee.

Fits into the Hive:
    Mirrors src/hivemind/wardens/nurse/accept.py and trace.py (codingrules section 3). The whole
    accept path runs through a real Warden in tests/unit/wardens/test_warden_nurse.py; this module
    covers the pieces that path cannot easily reach: an outage, the event's payload, and which
    proposals become actions.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.wardens.nurse.accept and hivemind.wardens.nurse.trace for the modules under test.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from builders.capping import make_action, make_nurse_request, make_proposal

from hivemind.forage import ForageMap
from hivemind.llm import ProviderUnavailableError, RateLimitedError
from hivemind.memory import MemoryIdentity
from hivemind.supervision.capping import CappingGate, NurseAnswer, NurseRequest, ProposalState
from hivemind.wardens.nurse import NurseTrace, NurseWiring, ask_nurse, nurse_event
from waggle.clock import FakeClock
from waggle.ids import new_cell_id, new_hive_id, new_node_id, new_task_id, new_worker_id
from waggle.messages.task import (
    NurseFinding,
    NurseOutcome,
    NurseReview,
    NurseReviewStatus,
    NurseVerdict,
)

_WORDS = "The report is a stub with a secret path in it."


class _DownReviewer:
    """A Nurse whose provider is down: the call never reaches a model."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def review(self, request: NurseRequest) -> NurseAnswer:
        raise self._error


@pytest.mark.parametrize(
    "error", [ProviderUnavailableError("judge", "down"), RateLimitedError("judge", 30.0)]
)
async def test_an_outage_is_not_nursed_rather_than_a_crash(error: Exception) -> None:
    wiring = NurseWiring(reviewer=_DownReviewer(error), forage=ForageMap((), clock=FakeClock()))

    review, spend = await ask_nurse(wiring, make_nurse_request(), 2)

    assert (review.status, review.round, review.nurse_grade, spend) == (
        NurseReviewStatus.UNAVAILABLE,
        2,
        None,
        0.0,
    )


def test_the_trail_event_carries_counts_never_a_findings_words() -> None:
    clock = FakeClock()
    finding = NurseFinding(path="report.md", line=3, problem=_WORDS, fix=_WORDS)
    verdict = NurseVerdict(
        outcome=NurseOutcome.CHANGES_REQUESTED, findings=(finding,), rubric_id="nurse-review/1"
    )
    review = NurseReview(
        status=NurseReviewStatus.REVIEWED, round=1, verdict=verdict, nurse_grade=4, worker_grade=3
    )
    identity = MemoryIdentity(
        hive_id=new_hive_id(clock), node_id=new_node_id(clock), actor="system"
    )

    event = nurse_event(review, new_task_id(clock), new_cell_id(clock), identity, clock)

    assert event.kind == "capping.nurse_reviewed"
    assert "tier" not in event.payload  # The Night Veil teardown's tier counts never see it.
    assert _WORDS not in event.model_dump_json() and "report.md" not in event.model_dump_json()
    assert (event.payload["outcome"], event.payload["findings"]) == ("CHANGES_REQUESTED", 1)


def test_only_a_bees_own_verified_proposals_become_actions() -> None:
    clock = FakeClock()
    bee, other = new_worker_id(clock), new_worker_id(clock)
    landed = make_action(summary="landed")
    proposals = (
        make_proposal(proposer=bee, action=landed, state=ProposalState.VERIFIED),
        make_proposal(proposer=bee, state=ProposalState.ROLLED_BACK),  # Never landed.
        make_proposal(proposer=other, state=ProposalState.VERIFIED),  # Not this bee's work.
    )
    gate = cast(CappingGate, SimpleNamespace(proposals=lambda: proposals))

    assert NurseTrace(gates=((bee, gate),)).actions() == (landed,)
