"""Test hivemind.cli.landing.errors: every refusal reads as one sentence an operator can act on.

Fits into the Hive:
    Mirrors src/hivemind/cli/landing/errors.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.
"""

from __future__ import annotations

import httpx

from hivemind.cli.landing import LandingRefusedError
from hivemind.entrance.gate import ErrorBody

_STEP_UP = "hivemind.entrance.step_up_required"


def _refusal(status: int, **fields: object) -> LandingRefusedError:
    """A refusal as the Entrance would send it."""
    body = ErrorBody.model_validate({"error": "hivemind.entrance.x", "detail": "No.", **fields})
    return LandingRefusedError(status, body)


def test_a_step_up_for_an_interactive_device_is_worth_asking_again() -> None:
    refusal = _refusal(403, error=_STEP_UP, reason="over_daily_cap")

    assert refusal.wants_step_up
    assert refusal.pending_id is None


def test_a_held_request_names_the_pending_confirmation_and_is_not_retried() -> None:
    refusal = _refusal(403, error=_STEP_UP, pending_id="pend_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3")

    assert not refusal.wants_step_up
    assert "held as pending confirmation pend_01J8Z3Q4X5Y6Z7A8B9C0D1E2F3" in str(refusal)


def test_a_failed_login_lists_what_it_may_mean_without_choosing() -> None:
    refusal = _refusal(401, error="hivemind.entrance.authentication_failed")

    assert "wrong, or this device may be pending approval, locked or revoked" in str(refusal)


def test_a_capability_denial_names_the_capability() -> None:
    refusal = _refusal(403, error="hivemind.entrance.capability_denied", capability="observe")

    assert "grant observe" in str(refusal)


def test_an_answer_that_is_not_the_entrances_refusal_says_only_its_status() -> None:
    response = httpx.Response(
        404, json={"detail": "Not Found"}, request=httpx.Request("POST", "http://x/v1/y")
    )

    refusal = LandingRefusedError.from_response(response)

    assert refusal.status == 404
    assert str(refusal).startswith("The Entrance answered HTTP 404.")
    assert "does not exist on the remote listener" in str(refusal)


def test_a_refusal_is_one_bounded_line() -> None:
    refusal = _refusal(409, detail="first line\nsecond line " + "x" * 1_000)

    text = str(refusal)

    assert "\n" not in text
    assert len(text) < 400
