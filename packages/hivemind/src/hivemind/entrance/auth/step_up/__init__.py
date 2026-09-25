"""Hold step-up: when it is required, how a session steps up, and the break-glass phrases.

A step-up at the Hive Entrance (the Hive's one HTTP door) re-runs the device's factors and keeps a
session stepped up for ``step_up_window_minutes`` (ADR-0041). ``rules`` decides, purely, when a
request needs one (a goal over ``step_up_spend`` or the device's daily cap, key and capability
changes, Supersedure, Sting Cut, Absconding, reopening, locking another device, and a session the
travel lock flagged); ``ceremony`` is the step-up itself, for interactive devices only;
``phrases`` holds the typed confirmation each break-glass action additionally needs.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by every
    route that guards a sensitive action and by the step-up route (later steps), the confirmation
    flow and the Entrance Reducer. Calls into login's factors and refusals and the session book.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A device no person types at never steps up; its requests wait as pending confirmations.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".

Public API:
    - ActionKind, StepUpReason, GoalSpend, BREAK_GLASS_ACTIONS, requires_step_up, action_step_up:
      when a step-up is required (rules).
    - step_up, step_up_challenge, STEP_UP_KIND: the step-up (ceremony).
    - BREAK_GLASS_PHRASES, check_break_glass, normalise_phrase: break-glass (phrases).
"""

from hivemind.entrance.auth.step_up.ceremony import STEP_UP_KIND, step_up, step_up_challenge
from hivemind.entrance.auth.step_up.phrases import (
    BREAK_GLASS_PHRASES,
    check_break_glass,
    normalise_phrase,
)
from hivemind.entrance.auth.step_up.rules import (
    BREAK_GLASS_ACTIONS,
    ActionKind,
    GoalSpend,
    StepUpReason,
    action_step_up,
    requires_step_up,
)

__all__ = [
    "BREAK_GLASS_ACTIONS",
    "BREAK_GLASS_PHRASES",
    "STEP_UP_KIND",
    "ActionKind",
    "GoalSpend",
    "StepUpReason",
    "action_step_up",
    "check_break_glass",
    "normalise_phrase",
    "requires_step_up",
    "step_up",
    "step_up_challenge",
]
