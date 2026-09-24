"""Tests for hivemind.entrance.auth.session.failures: the Guard signal a refusal leaves.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/session/failures.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.session.failures for the module under test.
"""

from __future__ import annotations

from builders.entrance import REMOTE, admitted_program, memory_enrolment

from hivemind.entrance.auth import Arrival, Listener
from hivemind.entrance.auth.session import (
    LOGIN_FAILED_KIND,
    Failure,
    FailureReason,
    FailureStep,
    record_failure,
)
from hivemind.entrance.errors import AuthenticationFailedError


async def test_a_refusal_is_on_the_trail_before_its_generic_error_is_handed_back() -> None:
    rig = memory_enrolment()
    device, _ = await admitted_program(rig)
    failure = Failure(
        FailureReason.PASSWORD, FailureStep.LOGIN, REMOTE, device.id, {"consecutive_failures": 2}
    )

    error = await record_failure(rig.deps.records, failure)

    assert isinstance(error, AuthenticationFailedError)
    (event,) = await rig.events(LOGIN_FAILED_KIND)
    assert (event.subject_id, event.actor) == (device.id, "system")
    assert event.payload == {
        "reason": "password",
        "step": "login",
        "listener": "remote",
        "address": "100.64.0.7",
        "consecutive_failures": 2,
    }


async def test_a_refusal_about_no_known_device_is_about_the_hive() -> None:
    rig = memory_enrolment()
    odd = Arrival(Listener.LOOPBACK, "a forged\nhost")

    await record_failure(
        rig.deps.records, Failure(FailureReason.DEVICE, FailureStep.CHALLENGE, odd)
    )

    (event,) = await rig.events(LOGIN_FAILED_KIND)
    assert event.subject_id == rig.deps.records.identity.hive_id
    assert event.payload["address"] == "unreadable"
