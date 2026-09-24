"""Tests for hivemind.entrance.auth.step_up.phrases: break-glass needs its typed phrase.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/step_up/phrases.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.step_up.phrases for the module under test.
"""

from __future__ import annotations

import pytest
from builders.entrance import make_device, make_session

from hivemind.entrance.auth import BREAK_GLASS_PHRASES, ActionKind, AuthenticatedSession
from hivemind.entrance.auth.step_up import BREAK_GLASS_ACTIONS, check_break_glass
from hivemind.entrance.enrol import DeviceStatus
from hivemind.entrance.errors import BreakGlassRefusedError
from hivemind.guard import CapabilitySet
from waggle.clock import FakeClock


def _session(interactive: bool = True, stepped_up: bool = True) -> AuthenticatedSession:
    """An authenticated session of an APPROVED device."""
    clock = FakeClock()
    device = make_device(clock, DeviceStatus.APPROVED, interactive=interactive)
    return AuthenticatedSession(
        make_session(device.id, clock), device, CapabilitySet.empty(), stepped_up
    )


def test_every_break_glass_action_has_its_own_phrase() -> None:
    assert set(BREAK_GLASS_PHRASES) == BREAK_GLASS_ACTIONS
    assert len(set(BREAK_GLASS_PHRASES.values())) == len(BREAK_GLASS_ACTIONS)


@pytest.mark.parametrize("phrase", ["abscond my hive", "Abscond  my Hive ", "ABSCOND MY HIVE"])
def test_absconding_passes_with_its_phrase_as_a_person_types_it(phrase: str) -> None:
    check_break_glass(ActionKind.ABSCOND, _session(), phrase)


@pytest.mark.parametrize(
    ("session", "phrase"),
    [
        (_session(), None),
        (_session(), "abscond"),
        (_session(), BREAK_GLASS_PHRASES[ActionKind.STING_CUT]),  # Another action's phrase.
        (_session(interactive=False), "abscond my hive"),
        (_session(stepped_up=False), "abscond my hive"),
    ],
)
def test_absconding_is_refused_without_all_it_needs(
    session: AuthenticatedSession, phrase: str | None
) -> None:
    with pytest.raises(BreakGlassRefusedError):
        check_break_glass(ActionKind.ABSCOND, session, phrase)


def test_an_action_that_is_not_break_glass_needs_no_phrase() -> None:
    check_break_glass(ActionKind.GOAL, _session(interactive=False, stepped_up=False), None)
