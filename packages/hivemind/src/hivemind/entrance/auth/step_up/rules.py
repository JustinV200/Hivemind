"""Decide, purely, whether a request needs a step-up the session does not have.

A step-up re-runs the device's factors and keeps a session stepped up for
``step_up_window_minutes`` (ADR-0033). It is required for: a goal whose budget exceeds
``step_up_spend``; a goal that would take the submitting device past its daily spend cap (the caller
supplies what the device has spent today); key and capability changes; Supersedure; Sting Cut;
Absconding; reopening a reduced Entrance; locking another device. The travel lock adds one more: a
session opened from a network the device has not used before must step up before anything else.
``ActionKind`` names those actions (and ``NEW_NETWORK``, the travel lock's own, which a person
confirms for a device that cannot step up); ``requires_step_up`` answers with the reason, or None.
Three of the actions are break-glass (``BREAK_GLASS_ACTIONS``) and additionally need their typed
phrase (``hivemind.entrance.auth.step_up.phrases``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.step_up``. Called
    by every route that guards one of these actions (later steps), by the confirmation flow and
    by the Entrance Reducer (reopening). Calls into ``hivemind.entrance.auth.session.models`` and
    ``hivemind.entrance.enrol.models`` only; pure, no I/O.

Key invariants:
    - A session the travel lock flagged needs step-up for every request, whatever it asks.
    - Every action but a goal within both limits needs step-up; a stepped-up session needs none
      (break-glass still needs its phrase).

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
    - hivemind.entrance.auth.step_up.ceremony for the step-up itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hivemind.entrance.auth.session.models import AuthenticatedSession
from hivemind.entrance.enrol.models import EnrolledDevice

__all__ = [
    "BREAK_GLASS_ACTIONS",
    "ActionKind",
    "GoalSpend",
    "StepUpReason",
    "action_step_up",
    "requires_step_up",
]


class ActionKind(Enum):
    """An action the Entrance may have to guard with step-up; also a held confirmation's kind."""

    GOAL = "goal"  # Submit a goal: guarded when over step_up_spend or the daily cap.
    KEY_CHANGE = "key_change"  # Rotate or replace a device's key.
    CAPABILITY_CHANGE = "capability_change"  # Change what a device may do.
    SUPERSEDURE = "supersedure"  # Move the Hive Stand (break-glass).
    STING_CUT = "sting_cut"  # Disconnect a Cell at once (break-glass).
    ABSCOND = "abscond"  # Tear every Cell and lease down (break-glass).
    REOPEN = "reopen"  # Reopen a reduced Entrance (loopback only).
    LOCK_DEVICE = "lock_device"  # Lock another device, e.g. a lost phone.
    NEW_NETWORK = "new_network"  # Trust the network the travel lock flagged for a device.


class StepUpReason(Enum):
    """Why a request needs a step-up; the route answers ``403 step_up_required`` with it."""

    OVER_STEP_UP_SPEND = "over_step_up_spend"  # The goal's budget exceeds step_up_spend.
    OVER_DAILY_CAP = "over_daily_cap"  # The goal would take the device past its daily cap.
    SENSITIVE_ACTION = "sensitive_action"  # A key, capability, break-glass or door action.
    NEW_NETWORK = "new_network"  # The travel lock saw the device on a network it never used.


# Absconding, Sting Cut and Supersedure: codingrules 15's typed phrase, interactive devices only.
BREAK_GLASS_ACTIONS = frozenset({ActionKind.ABSCOND, ActionKind.STING_CUT, ActionKind.SUPERSEDURE})


@dataclass(frozen=True, slots=True)
class GoalSpend:
    """What a goal would spend, and what its device has spent today (the caller supplies both).

    Attributes:
        budget_usd: The goal's budget, in USD; must be >= 0.
        spent_today_usd: What the submitting device has spent today, in USD; must be >= 0.
    """

    budget_usd: float
    spent_today_usd: float

    def __post_init__(self) -> None:
        """Refuse a negative amount, which would make every comparison below meaningless."""
        if self.budget_usd < 0 or self.spent_today_usd < 0:
            raise ValueError("A goal's budget and a day's spend are never negative.")


def action_step_up(
    action: ActionKind, device: EnrolledDevice, spend: GoalSpend | None, step_up_spend: float
) -> StepUpReason | None:
    """Return why ``action`` needs step-up whoever asks, or None when it does not.

    Args:
        action: What is being asked for.
        device: The device asking; its daily spend cap bounds a goal.
        spend: A goal's budget and the device's spend today; required for ``GOAL``.
        step_up_spend: ``[entrance] step_up_spend``, in USD.

    Returns:
        The reason, or None for a goal within both limits.

    Raises:
        ValueError: ``action`` is ``GOAL`` and ``spend`` is None.
    """
    # Every action but a goal is sensitive by itself (ADR-0033's list).
    if action is not ActionKind.GOAL:
        return StepUpReason.SENSITIVE_ACTION
    if spend is None:
        raise ValueError("A goal's step-up decision needs its budget and the day's spend.")
    if spend.budget_usd > step_up_spend:
        return StepUpReason.OVER_STEP_UP_SPEND
    cap = device.spend_cap_usd_per_day
    # An uncapped device (only the console) has no daily cap to pass.
    if cap is not None and spend.spent_today_usd + spend.budget_usd > cap:
        return StepUpReason.OVER_DAILY_CAP
    return None


def requires_step_up(
    session: AuthenticatedSession,
    action: ActionKind | None = None,
    spend: GoalSpend | None = None,
    *,
    step_up_spend: float,
) -> StepUpReason | None:
    """Return why this request needs a step-up the session does not have, or None.

    Args:
        session: The authenticated session making the request.
        action: What it asks for; None for a request that is none of the guarded actions.
        spend: A goal's budget and the device's spend today; required for ``GOAL``.
        step_up_spend: ``[entrance] step_up_spend``, in USD.

    Returns:
        The reason to answer ``403 step_up_required`` with, or None when the request may proceed
        (break-glass still needs its phrase: ``check_break_glass``).

    Raises:
        ValueError: ``action`` is ``GOAL`` and ``spend`` is None.
    """
    # The travel lock's flag comes first: a new network steps up before anything else. Stepping
    # up clears it, so a flagged session is never also stepped up.
    if session.needs_step_up:
        return StepUpReason.NEW_NETWORK
    if action is None:
        return None
    reason = action_step_up(action, session.device, spend, step_up_spend)
    if reason is None or session.stepped_up:
        return None
    return reason
