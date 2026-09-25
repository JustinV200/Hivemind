"""Step a session up: re-run the device's factors, and keep it stepped up for a short window.

A step-up at the Hive Entrance (the Hive's one HTTP door) needs a person (ADR-0041): a fresh passkey
assertion with user verification, or, on an interactive Ed25519 device (the console, a program the
operator approved as interactive), a fresh device proof plus the password typed at it. It marks the
session stepped up for ``step_up_window_minutes``, clears the travel lock's flag (and remembers
the network the person just cleared), records ``guard.entrance_step_up`` and tells every other
device. A device no person types at cannot step up at all (``StepUpUnavailableError``): what it
asks for that needs step-up is held as a pending confirmation instead
(``hivemind.entrance.auth.confirm``). ``step_up_challenge`` issues the challenge, bound to the
session itself, so a login challenge can never be spent as a step-up nor one session's step-up by
another. The failures count exactly like login's: an invalid proof against the address, a wrong
password against the device, with the same lockout.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.step_up``. Called
    by the step-up route (a later step). Calls into the login factors and refusals, the session
    book, the travel lock and the Entrance tables through ``AuthDeps``.

Key invariants:
    - Only an interactive device steps up; a passkey needs user verification, an Ed25519 device
      the password as well.
    - A step-up challenge is bound to one session and spent once.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
    - hivemind.entrance.auth.step_up.rules for when a step-up is required.
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.keys import KeyKind
from hivemind.entrance.auth.login.deps import AuthDeps
from hivemind.entrance.auth.login.factors import DeviceProof, password_holds, verify_proof
from hivemind.entrance.auth.login.flow import (
    LoginChallenge,
    login_refusal,
    passkey_request_options,
)
from hivemind.entrance.auth.login.refusals import refuse, refuse_password, refuse_proof
from hivemind.entrance.auth.session.failures import Failure, FailureReason, FailureStep
from hivemind.entrance.auth.session.models import Arrival, AuthenticatedSession, Session
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.record import notify
from hivemind.entrance.errors import (
    ChallengeRejectedError,
    DeviceNotFoundError,
    DeviceStatusConflictError,
    PasskeyRejectedError,
    StepUpUnavailableError,
)

STEP_UP_KIND = "guard.entrance_step_up"  # A session re-ran its factors.

log = get_logger(__name__)

__all__ = ["STEP_UP_KIND", "step_up", "step_up_challenge"]


def step_up_challenge(deps: AuthDeps, session: AuthenticatedSession) -> LoginChallenge:
    """Issue a step-up challenge bound to ``session``.

    Args:
        deps: The login dependencies.
        session: The authenticated session asking to step up.

    Returns:
        The challenge and, for a passkey device, its WebAuthn request options.

    Raises:
        StepUpUnavailableError: The device is not interactive.
    """
    if not session.interactive:
        raise StepUpUnavailableError(session.device.id)
    # Bound to the session's token hash: only this session can spend it, and never as a login.
    challenge = deps.ceremony.challenges.issue(session.device.id, session.session.token_hash)
    return LoginChallenge(
        device_id=session.device.id,
        nonce=challenge.nonce,
        expires_at=challenge.expires_at,
        passkey_options=passkey_request_options(deps, session.device, challenge.challenge_bytes),
    )


async def step_up(
    deps: AuthDeps,
    session: AuthenticatedSession,
    proof: DeviceProof,
    password: str | None,
    arrival: Arrival,
) -> Session:
    """Re-run the factors on ``session`` and mark it stepped up for the window.

    Args:
        deps: The login dependencies.
        session: The authenticated session stepping up.
        proof: A fresh proof over its step-up challenge.
        password: The operator password: required for an Ed25519 device; optional for a passkey
            (its user verification is the person), and checked whenever given.
        arrival: The listener and address the step-up came from.

    Returns:
        The session as stored, stepped up.

    Raises:
        StepUpUnavailableError: The device is not interactive.
        AuthenticationFailedError: A factor failed, or the device may no longer log in.
    """
    device = session.device
    if not session.interactive:
        raise StepUpUnavailableError(device.id)
    try:
        challenge = deps.ceremony.challenges.take(
            proof.nonce, device.id, session.session.token_hash
        )
    except ChallengeRejectedError:
        raise await refuse_proof(deps, arrival, FailureStep.STEP_UP, device.id) from None
    current = await _current(deps, device)
    reason = await login_refusal(deps, current, arrival)
    if current is None or reason is not None:
        known = current.id if current is not None else None
        failure = Failure(reason or FailureReason.DEVICE, FailureStep.STEP_UP, arrival, known)
        raise await refuse(deps, failure)
    verified = verify_proof(deps.ceremony, deps.records.identity.hive_id, current, proof, challenge)
    if verified is None:
        raise await refuse_proof(deps, arrival, FailureStep.STEP_UP, device.id)
    # A passkey's user verification is the person; an Ed25519 key needs the typed password.
    needs_password = current.key_kind is not KeyKind.PASSKEY
    if (needs_password and password is None) or (
        password is not None
        and not await password_holds(deps.ceremony.hasher, deps.records.store, password)
    ):
        raise await refuse_password(deps, device.id, arrival, FailureStep.STEP_UP)
    return await _mark(deps, session, arrival, verified.sign_count)


async def _current(deps: AuthDeps, device: EnrolledDevice) -> EnrolledDevice | None:
    """Re-read the device: it may have been locked since this session's request was admitted."""
    try:
        # Latency: one local primary-key read.
        return await deps.records.store.get_device(device.id)
    except DeviceNotFoundError:
        return None


async def _mark(
    deps: AuthDeps, session: AuthenticatedSession, arrival: Arrival, sign_count: int | None
) -> Session:
    """Record the fresh login on the device, mark the session, trust its network, record it."""
    records = deps.records
    now = records.clock.now()
    device_id = session.device.id
    try:
        # A passkey's counter must move forward here too; the device must still be APPROVED.
        await records.store.record_login(device_id, now, session.session.network, sign_count)
    except PasskeyRejectedError:
        # The counter did not move forward against the row itself: a racing or cloned passkey.
        raise await refuse_proof(deps, arrival, FailureStep.STEP_UP, device_id) from None
    except (DeviceStatusConflictError, DeviceNotFoundError):
        # Locked or revoked while its factors were being checked: that decision stands.
        failure = Failure(FailureReason.DEVICE, FailureStep.STEP_UP, arrival, device_id)
        raise await refuse(deps, failure) from None
    # Latency: one local delete, then one local session update.
    await records.store.logins.clear_failures(device_id)
    stepped = await deps.sessions.mark_stepped_up(session.session, now)
    if stepped is None:
        # The session ended while its factors were being checked: nothing is left to step up.
        raise await refuse(
            deps, Failure(FailureReason.DEVICE, FailureStep.STEP_UP, arrival, device_id)
        )
    travel = deps.guards.travel
    # The person just cleared the network the travel lock flagged; it is known from now on.
    if session.needs_step_up and travel is not None:
        await travel.trust(device_id, session.session.network)
    payload: dict[str, JsonValue] = {
        "key_kind": session.device.key_kind.value if session.device.key_kind else None,
        "listener": arrival.listener.value,
        "address": arrival.trail_address,
        "cleared_travel_lock": session.needs_step_up,
        "window_minutes": deps.sessions.rules.step_up_window.total_seconds() / 60,
    }
    event = records.identity.event(records.clock, STEP_UP_KIND, device_id, payload, device_id)
    # Latency: one local trail write, then the notifier queues its notice and returns.
    await records.trail.record(event)
    await notify(deps.enrolment, device_id, event)
    log.info("entrance.stepped_up", device_id=device_id)
    return stepped
