"""Tests for hivemind.entrance.auth.confirm.models: the held request and its ids.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/confirm/models.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.confirm.models for the module under test.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from builders.entrance import make_pending
from pydantic import ValidationError

from hivemind.entrance.auth.confirm import (
    MAX_HELD_PAYLOAD_BYTES,
    PENDING_ID_PREFIX,
    PendingConfirmation,
    PendingStatus,
    new_pending_id,
)
from waggle.clock import FakeClock
from waggle.ids import DeviceId

_DEVICE = DeviceId("device_01M221E4C10R4XDPNQNRX85AAA")
_OTHER = DeviceId("device_01M221E4C10R4XDPNQNRX85AAB")


def test_a_pending_confirmation_round_trips_through_json() -> None:
    clock = FakeClock()
    confirmed = make_pending(
        _DEVICE,
        clock,
        status=PendingStatus.CONFIRMED,
        settled_at=clock.now(),
        confirmed_by=_OTHER,
    )

    assert PendingConfirmation.model_validate_json(confirmed.model_dump_json()) == confirmed


def test_pending_ids_are_prefixed_ulids_that_sort_by_time() -> None:
    clock = FakeClock()
    first = new_pending_id(clock)
    clock.advance(1)
    second = new_pending_id(clock)

    assert first.startswith(PENDING_ID_PREFIX)
    assert first < second


@pytest.mark.parametrize(
    "overrides",
    [
        {"payload": {"blob": "x" * MAX_HELD_PAYLOAD_BYTES}},  # Too large to hold.
        {"status": PendingStatus.EXPIRED},  # Settled without settled_at.
        {"settled_at": FakeClock().now()},  # settled_at while still PENDING.
        {"status": PendingStatus.CONFIRMED, "settled_at": FakeClock().now()},  # No confirmer.
        {"confirmed_by": _OTHER},  # A confirmer while still PENDING.
        {"expires_at": FakeClock().now() - timedelta(seconds=1)},  # Expired before held.
        {"id": "task_01M221E4C10R4XDPNQNRX85AAA"},  # Not a pend_ id.
        {"action": "take_over"},  # Not an action kind.
    ],
)
def test_a_pending_confirmation_refuses_an_inconsistent_record(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_pending(_DEVICE, FakeClock(), **overrides)
