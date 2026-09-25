"""Tests for hivemind.queen.intake.model: GoalRequest round-trips and refuses bad shapes.

Fits into the Hive:
    Mirrors src/hivemind/queen/intake/model.py (codingrules section 3; 14.3: every pydantic
    boundary model has a round-trip test and a rejection test).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.queen.intake.model for GoalRequest.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from builders.human import make_goal_request
from pydantic import ValidationError

from hivemind.cell import CombShieldLevel, RequestOrigin
from hivemind.queen.intake import (
    MAX_GOAL_TEXT_CHARS,
    GoalRequest,
    GoalRequestState,
    GoalSource,
    new_goal_request_id,
)
from waggle.clock import FakeClock
from waggle.ids import new_device_id, new_task_id


def test_a_full_request_round_trips_through_json() -> None:
    clock = FakeClock()
    request = make_goal_request(
        clock,
        budget_usd=3.0,
        comb_shield=CombShieldLevel.PROPOLIS,
        device_id=new_device_id(clock),
        capabilities=("llm:*", "cell:virtual"),
        source=GoalSource.SPOKEN,
        needs_confirmation=True,
    )

    assert GoalRequest.model_validate_json(request.model_dump_json()) == request


def test_the_capability_ceiling_is_stored_canonical() -> None:
    request = make_goal_request(FakeClock(), capabilities=("llm:*", "cell:virtual", "llm:*"))

    assert request.capabilities == ("cell:virtual", "llm:*")


def test_a_minted_id_matches_its_own_pattern() -> None:
    assert new_goal_request_id(FakeClock()).startswith("goalreq_")


@pytest.mark.parametrize(
    "overrides",
    [
        {"id": "task_01J0000000000000000000000A"},
        {"text": ""},
        {"text": "x" * (MAX_GOAL_TEXT_CHARS + 1)},
        {"budget_usd": 0.0},
        {"capabilities": ("not a capability",)},
        {"state": GoalRequestState.PLANNED},  # PLANNED needs its goal id.
        {"state": GoalRequestState.REFUSED},  # REFUSED needs its reason.
        {"refusal": "No."},  # A reason on a request that was never refused.
        {"confirmed_at": FakeClock().now()},  # Confirmed without ever being held.
        {"finished_at": FakeClock().now()},  # Finished before being planned.
        {"comb_shield": CombShieldLevel.NIGHT_VEIL, "origin": RequestOrigin.QUEEN},
        {"comb_shield": CombShieldLevel.NIGHT_VEIL, "origin": RequestOrigin.WARDEN},
    ],
    ids=str,
)
def test_a_malformed_request_is_refused(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_goal_request(FakeClock(), **overrides)


def test_goal_id_is_refused_on_a_request_that_is_not_planned() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        make_goal_request(clock, goal_id=new_task_id(clock))


def test_updated_at_may_not_precede_received_at() -> None:
    clock = FakeClock()

    with pytest.raises(ValidationError):
        make_goal_request(clock, updated_at=clock.now() - timedelta(seconds=1))


def test_night_veil_from_a_human_is_accepted() -> None:
    request = make_goal_request(FakeClock(), comb_shield=CombShieldLevel.NIGHT_VEIL)

    assert request.origin is RequestOrigin.HUMAN
