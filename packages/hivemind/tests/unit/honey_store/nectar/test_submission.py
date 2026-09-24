"""Tests for hivemind.honey_store.nectar.submission: intake's value shapes and deposit conversion.

Fits into the Hive:
    Mirrors src/hivemind/honey_store/nectar/submission.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.honey_store.nectar.submission for the module under test.
"""

from __future__ import annotations

import pytest
from builders.honey import make_nectar_deposit, make_nectar_submission
from pydantic import ValidationError

from hivemind.cell import CombShieldLevel, HoneyClearance
from hivemind.honey_store.models import NectarOrigin
from hivemind.honey_store.nectar.submission import (
    DepositSource,
    NectarSubmission,
    handoff_source_key,
    submission_from_deposit,
)
from waggle.clock import FakeClock
from waggle.ids import CellId, new_cell_id, new_event_id
from waggle.messages import CombShieldLevel as WireCombShieldLevel
from waggle.messages import HoneyClearance as WireHoneyClearance
from waggle.messages.honey import NectarKind


def _source(cell_id: CellId, *, borrowed: bool = True) -> DepositSource:
    """Build a DepositSource for `cell_id`: a borrowed MEADOW Cell unless told otherwise."""
    return DepositSource(
        sender="warden_relay",
        cell_id=cell_id,
        from_borrowed_cell=borrowed,
        tier=CombShieldLevel.MEADOW,
    )


def test_nectar_submission_round_trips_through_json() -> None:
    submission = make_nectar_submission(proposed_scope="task:task_x", source_key="wax:wax_1")

    restored = NectarSubmission.model_validate_json(submission.model_dump_json())

    assert restored == submission


@pytest.mark.parametrize(
    "overrides",
    [
        {"content": b""},
        {"proposed_scope": "folder/with/slashes"},
        {"media_type": ""},
        {"unexpected": True},
    ],
)
def test_nectar_submission_refuses_malformed_input(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        make_nectar_submission(FakeClock(), **overrides)


def test_handoff_source_key_matches_adr_0031s_spelling() -> None:
    event_id = new_event_id(FakeClock())

    assert handoff_source_key(event_id) == f"handoff:{event_id}"


def test_submission_from_deposit_trusts_the_queens_record_over_the_senders_claim() -> None:
    deposit = make_nectar_deposit(
        clearance=WireHoneyClearance.C0, origin_tier=WireCombShieldLevel.NIGHT_VEIL
    )

    submission = submission_from_deposit(deposit, deposit.chunk, _source(deposit.cell_id))

    assert submission.origin is NectarOrigin.BEE
    assert submission.declared is HoneyClearance.C0
    assert submission.tier is CombShieldLevel.MEADOW
    assert submission.from_borrowed_cell is True
    assert submission.bee == deposit.worker_id
    assert submission.content == deposit.chunk
    assert submission.source_key is None


def test_submission_from_deposit_gives_a_handoff_its_shared_dedupe_key() -> None:
    clock = FakeClock()
    event_id = new_event_id(clock)
    deposit = make_nectar_deposit(
        clock, kind=NectarKind.HANDOFF, event_id=event_id, media_type="application/json"
    )

    submission = submission_from_deposit(
        deposit, deposit.chunk, _source(deposit.cell_id, borrowed=False)
    )

    assert submission.source_key == handoff_source_key(event_id)
    assert submission.event_id == event_id


def test_submission_from_deposit_leaves_bee_empty_for_a_wardens_own_deposit() -> None:
    deposit = make_nectar_deposit(worker_id=None, kind=NectarKind.PATROL_SUMMARY, task_id=None)

    submission = submission_from_deposit(deposit, deposit.chunk, _source(new_cell_id(FakeClock())))

    assert submission.bee is None
    assert submission.task_id is None
