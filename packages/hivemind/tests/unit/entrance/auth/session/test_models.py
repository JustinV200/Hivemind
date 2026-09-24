"""Tests for hivemind.entrance.auth.session.models: the session records and their rules.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/session/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.session.models for the module under test.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from builders.entrance import BrowserKey, make_session
from pydantic import ValidationError

from hivemind.entrance.auth import Arrival, BindingKind, EndReason, Listener, Session
from waggle.clock import FakeClock
from waggle.ids import DeviceId

_DEVICE = DeviceId("device_01M221E4C10R4XDPNQNRX85AAA")


def test_a_session_round_trips_through_json() -> None:
    clock = FakeClock()
    session = make_session(
        _DEVICE,
        clock,
        binding_kind=BindingKind.P256,
        binding_key=BrowserKey().public_key,
        stepped_up_until=clock.now() + timedelta(minutes=5),
        ended_at=clock.now(),
        end_reason=EndReason.LOGOUT,
    )

    assert Session.model_validate_json(session.model_dump_json()) == session


@pytest.mark.parametrize(
    "overrides",
    [
        {"token_hash": "A" * 64},  # Not lowercase hex.
        {"binding_kind": BindingKind.P256},  # An Ed25519-sized key for a P-256 binding.
        {"binding_key": "AQID"},  # Neither kind's size.
        {"network": "100.64.3.7/24"},  # Host bits set.
        {"ended_at": FakeClock().now()},  # An end without its reason.
        {"end_reason": EndReason.IDLE},  # A reason without its end.
        {"expires_at": FakeClock().now()},  # Expires the moment it opens.
        {"trusted": True},
    ],
)
def test_a_session_refuses_an_inconsistent_record(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_session(_DEVICE, FakeClock(), **overrides)


def test_stepped_up_holds_only_inside_the_window_and_never_while_the_travel_lock_owes_one() -> None:
    clock = FakeClock()
    until = clock.now() + timedelta(minutes=5)
    session = make_session(_DEVICE, clock, stepped_up_until=until)
    flagged = make_session(_DEVICE, clock, stepped_up_until=until, needs_step_up=True)

    assert session.stepped_up_at(clock.now())
    assert not session.stepped_up_at(until)
    assert not flagged.stepped_up_at(clock.now())
    assert not make_session(_DEVICE, clock).stepped_up_at(clock.now())


def test_an_arrival_reports_a_trail_safe_address_and_its_network() -> None:
    overlay = Arrival(Listener.REMOTE, "100.64.0.7")
    odd = Arrival(Listener.LOOPBACK, "a host\nname")

    assert (overlay.trail_address, overlay.network) == ("100.64.0.7", "100.64.0.0/24")
    assert (odd.trail_address, odd.network) == ("unreadable", None)
