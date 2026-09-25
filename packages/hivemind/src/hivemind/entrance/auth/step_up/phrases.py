"""Hold the break-glass phrases, and refuse a break-glass action that lacks its phrase.

Absconding (tear every Cell and lease down), Sting Cut (disconnect a Cell at once) and Supersedure
(move the Hive Stand) are break-glass actions: besides a step-up, each needs the typed confirmation
phrase of codingrules 15 in the request, on every path the API included, and only from a device a
person types at (ADR-0041). The phrase is not a secret; it is proof of intent, the one thing a
misclick, a replayed request or an over-eager program cannot supply. So it is a constant per
action, compared after Unicode NFKC normalisation, case folding and whitespace collapsing (a phone
capitalises the first letter; a person may double a space).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.step_up``. Called
    by the routes and commands that carry out a break-glass action (later steps) and by the
    confirmation flow. Calls into ``hivemind.entrance.auth.step_up.rules`` and the session models.

Key invariants:
    - Every action in ``BREAK_GLASS_ACTIONS`` has exactly one phrase here.
    - ``check_break_glass`` passes a break-glass action only for an interactive, stepped-up
      session presenting the action's own phrase; any other action passes unchecked.

See Also:
    - .claude/codingrules.md section 15 for Absconding's re-auth and typed confirmation.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping

from hivemind.entrance.auth.session.models import AuthenticatedSession
from hivemind.entrance.auth.step_up.rules import BREAK_GLASS_ACTIONS, ActionKind
from hivemind.entrance.errors import BreakGlassRefusedError

# What a person types to confirm each break-glass action; shown to them by the client, verbatim.
BREAK_GLASS_PHRASES: Mapping[ActionKind, str] = {
    ActionKind.ABSCOND: "abscond my hive",
    ActionKind.STING_CUT: "sting cut this cell",
    ActionKind.SUPERSEDURE: "supersede my hive stand",
}

__all__ = ["BREAK_GLASS_PHRASES", "check_break_glass", "normalise_phrase"]


def normalise_phrase(text: str) -> str:
    """Return ``text`` as phrases are compared: NFKC, case-folded, single-spaced, trimmed.

    Args:
        text: A phrase as typed.

    Returns:
        Its comparable form.
    """
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def check_break_glass(
    action: ActionKind, session: AuthenticatedSession, phrase: str | None
) -> None:
    """Refuse a break-glass ``action`` without its phrase, a step-up, or an interactive device.

    Args:
        action: What the request asks for.
        session: The authenticated session asking.
        phrase: The typed confirmation phrase the request carries, or None.

    Raises:
        BreakGlassRefusedError: ``action`` is break-glass and the device is not interactive, the
            session is not stepped up, or the phrase is missing or not the action's own.
    """
    if action not in BREAK_GLASS_ACTIONS:
        return
    # ADR-0041: only from an interactive device, after step-up, with the typed phrase.
    if not session.interactive:
        raise BreakGlassRefusedError(
            f"{action.value} is break-glass: only a device a person types at may ask for it."
        )
    if not session.stepped_up:
        raise BreakGlassRefusedError(f"{action.value} is break-glass: step up first.")
    expected = normalise_phrase(BREAK_GLASS_PHRASES[action])
    if phrase is None or normalise_phrase(phrase) != expected:
        raise BreakGlassRefusedError(
            f"{action.value} is break-glass: type the phrase {BREAK_GLASS_PHRASES[action]!r} to "
            "confirm it."
        )
