"""Tests for hivemind.entrance.auth.step_up.rules: when a request needs a step-up.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/step_up/rules.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.step_up.rules for the module under test.
"""

from __future__ import annotations

import pytest
from builders.entrance import make_device, make_session

from hivemind.entrance.auth import (
    ActionKind,
    AuthenticatedSession,
    GoalSpend,
    StepUpReason,
    requires_step_up,
)
from hivemind.entrance.enrol import DeviceStatus
from hivemind.guard import CapabilitySet
from waggle.clock import FakeClock

_STEP_UP_SPEND = 5.0  # [entrance] step_up_spend's default.
_SENSITIVE = [action for action in ActionKind if action is not ActionKind.GOAL]


def _session(
    stepped_up: bool = False, needs_step_up: bool = False, cap: float | None = 5.0
) -> AuthenticatedSession:
    """An authenticated session of an APPROVED device with a daily cap of ``cap``."""
    clock = FakeClock()
    device = make_device(clock, DeviceStatus.APPROVED, spend_cap_usd_per_day=cap)
    session = make_session(device.id, clock, needs_step_up=needs_step_up)
    return AuthenticatedSession(session, device, CapabilitySet.empty(), stepped_up)


def _needs(
    session: AuthenticatedSession, action: ActionKind | None, spend: GoalSpend | None
) -> StepUpReason | None:
    """Ask the rule with the default step_up_spend."""
    return requires_step_up(session, action, spend, step_up_spend=_STEP_UP_SPEND)


@pytest.mark.parametrize(
    ("spend", "reason"),
    [
        (GoalSpend(budget_usd=2.0, spent_today_usd=1.0), None),
        (GoalSpend(budget_usd=5.0, spent_today_usd=0.0), None),  # At the limit, not above it.
        (GoalSpend(budget_usd=5.01, spent_today_usd=0.0), StepUpReason.OVER_STEP_UP_SPEND),
        (GoalSpend(budget_usd=2.0, spent_today_usd=3.5), StepUpReason.OVER_DAILY_CAP),
    ],
)
def test_a_goal_needs_step_up_over_step_up_spend_or_past_the_daily_cap(
    spend: GoalSpend, reason: StepUpReason | None
) -> None:
    assert _needs(_session(), ActionKind.GOAL, spend) is reason


def test_an_uncapped_device_is_never_past_a_daily_cap() -> None:
    spend = GoalSpend(budget_usd=4.0, spent_today_usd=1_000.0)

    assert _needs(_session(cap=None), ActionKind.GOAL, spend) is None


@pytest.mark.parametrize("action", _SENSITIVE)
def test_every_other_guarded_action_needs_step_up_until_stepped_up(action: ActionKind) -> None:
    assert _needs(_session(), action, None) is StepUpReason.SENSITIVE_ACTION
    assert _needs(_session(stepped_up=True), action, None) is None


def test_a_stepped_up_session_may_spend_past_both_limits() -> None:
    spend = GoalSpend(budget_usd=50.0, spent_today_usd=50.0)

    assert _needs(_session(stepped_up=True), ActionKind.GOAL, spend) is None


def test_the_travel_lock_needs_step_up_before_anything_else() -> None:
    flagged = _session(needs_step_up=True)
    small = GoalSpend(budget_usd=1.0, spent_today_usd=0.0)

    assert _needs(flagged, None, None) is StepUpReason.NEW_NETWORK
    assert _needs(flagged, ActionKind.GOAL, small) is StepUpReason.NEW_NETWORK
    assert _needs(_session(), None, None) is None


def test_a_goal_without_its_spend_and_a_negative_spend_are_refused() -> None:
    with pytest.raises(ValueError, match="budget"):
        _needs(_session(), ActionKind.GOAL, None)
    with pytest.raises(ValueError, match="negative"):
        GoalSpend(budget_usd=-1.0, spent_today_usd=0.0)
