"""Log a device in: issue its challenge, then check its key proof first and the password second.

Login at the Hive Entrance (the Hive's one HTTP door) is the device's key plus the operator's
password (ADR-0033). ``begin_login`` issues a single-use, 60-second challenge from the login
``ChallengeBook``, bound to the device and, for a browser, to the WebCrypto P-256 key its session
will be bound to (registering it here ties that key to the ceremony), with WebAuthn request
options for a passkey device; it refuses a device that is unknown, not APPROVED or lapsed, the
loopback-bound console on the remote listener, and any remote login while the Entrance is reduced.
``finish_login`` spends the challenge and **verifies the device proof first**: a proof that does
not hold never reaches the password check, is charged to the address and never to the device. A
valid proof with the wrong password counts against the device and locks it at
``lockout_attempts``. A success resets the count, records the login on the device (its last
sighting and network, a passkey's counter, which may only move forward), consults the travel lock,
and opens a session bound to the device's Ed25519 key (a program, the console) or the browser's
P-256 key (a passkey device), returning its token once. Every refusal is the same
``AuthenticationFailedError`` and a ``guard.entrance_login_failed`` event with the reason.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.login``. Called by
    the unauthenticated login routes (a later step), which apply the per-address rate limit
    first. Calls into this package's factors and refusals, the session book, the travel lock and
    the Entrance tables through ``AuthDeps``.

Key invariants:
    - The password is never checked before a device proof held.
    - A challenge is spent by ``finish_login`` whatever happens next.
    - The token is returned once and never stored, logged or recorded.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Login is the device
      key plus the password, the key proof first".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hivemind.entrance.auth.canonical import b64url_decode
from hivemind.entrance.auth.keys import KeyKind
from hivemind.entrance.auth.login.deps import AuthDeps
from hivemind.entrance.auth.login.factors import (
    DeviceProof,
    VerifiedProof,
    binding_fits,
    password_holds,
    verify_proof,
)
from hivemind.entrance.auth.login.refusals import refuse, refuse_password, refuse_proof
from hivemind.entrance.auth.passkeys import authentication_options
from hivemind.entrance.auth.session.book import OpenedSession, SessionGrant
from hivemind.entrance.auth.session.failures import Failure, FailureReason, FailureStep
from hivemind.entrance.auth.session.models import Arrival, BindingKind, Listener
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.state import DeviceStatus
from hivemind.entrance.errors import (
    ChallengeRejectedError,
    DeviceNotFoundError,
    DeviceStatusConflictError,
    PasskeyRejectedError,
)
from hivemind.entrance.reducer import EntranceMode
from waggle.ids import DeviceId

__all__ = [
    "LoginChallenge",
    "begin_login",
    "finish_login",
    "login_refusal",
    "passkey_request_options",
]


@dataclass(frozen=True, slots=True)
class LoginChallenge:
    """What a device answers to log in (or to step up).

    Attributes:
        device_id: The device it was issued for.
        nonce: The challenge, unpadded base64url; an Ed25519 device signs ``login_string`` over it.
        expires_at: When it can no longer be answered.
        passkey_options: For a passkey device, the ``PublicKeyCredentialRequestOptionsJSON`` to
            pass to ``navigator.credentials.get``; None for an Ed25519 device.
    """

    device_id: DeviceId
    nonce: str
    expires_at: datetime
    passkey_options: str | None


async def begin_login(
    deps: AuthDeps, device_id: DeviceId, arrival: Arrival, binding_key: str | None = None
) -> LoginChallenge:
    """Issue a login challenge for ``device_id``.

    Args:
        deps: The login dependencies.
        device_id: The device asking to log in.
        arrival: The listener and address the request came from.
        binding_key: A browser's WebCrypto P-256 session key (uncompressed point, unpadded
            base64url); None for an Ed25519 device.

    Returns:
        The challenge and, for a passkey device, its WebAuthn request options.

    Raises:
        AuthenticationFailedError: The device may not log in here now, or the binding key does
            not fit it; never says which.
    """
    device = await _device(deps, device_id)
    reason = await login_refusal(deps, device, arrival)
    if reason is None and device is not None and not binding_fits(device, binding_key):
        reason = FailureReason.PROOF
    if device is None or reason is not None:
        known = device.id if device is not None else None
        failure = Failure(reason or FailureReason.DEVICE, FailureStep.CHALLENGE, arrival, known)
        raise await refuse(deps, failure)
    challenge = deps.ceremony.challenges.issue(device.id, binding_key)
    return LoginChallenge(
        device_id=device.id,
        nonce=challenge.nonce,
        expires_at=challenge.expires_at,
        passkey_options=passkey_request_options(deps, device, challenge.challenge_bytes),
    )


async def finish_login(
    deps: AuthDeps, proof: DeviceProof, password: str, arrival: Arrival
) -> OpenedSession:
    """Answer a login challenge with the device proof and the password; open a session.

    Args:
        deps: The login dependencies.
        proof: The device's proof over its challenge (and a browser's binding key).
        password: The operator password, typed at the device (or held by a program).
        arrival: The listener and address the request came from.

    Returns:
        The session and its token, which the device receives once.

    Raises:
        AuthenticationFailedError: Any factor or check failed; never says which.
    """
    device = await _device(deps, proof.device_id)
    known = device.id if device is not None else None
    try:
        # Spent first, whatever follows: a refused answer can never be tried again.
        challenge = deps.ceremony.challenges.take(proof.nonce, proof.device_id, proof.binding_key)
    except ChallengeRejectedError:
        raise await refuse_proof(deps, arrival, FailureStep.LOGIN, known) from None
    reason = await login_refusal(deps, device, arrival)
    if device is None or reason is not None:
        failure = Failure(reason or FailureReason.DEVICE, FailureStep.LOGIN, arrival, known)
        raise await refuse(deps, failure)
    hive_id = deps.records.identity.hive_id
    verified = verify_proof(deps.ceremony, hive_id, device, proof, challenge)
    # The device proof first (ADR-0033): a bad one never reaches the password check.
    if verified is None:
        raise await refuse_proof(deps, arrival, FailureStep.LOGIN, device.id)
    if not await password_holds(deps.ceremony.hasher, deps.records.store, password):
        raise await refuse_password(deps, device.id, arrival, FailureStep.LOGIN)
    return await _open(deps, device, proof, verified, arrival)


async def login_refusal(
    deps: AuthDeps, device: EnrolledDevice | None, arrival: Arrival
) -> FailureReason | None:
    """Say why ``device`` may not log in (or step up) on ``arrival`` now, or None when it may.

    Args:
        deps: The login dependencies.
        device: The device as stored, or None when no record has its id.
        arrival: The listener and address the request came from.

    Returns:
        ``DEVICE`` for an unknown, unapproved or lapsed device (or a passkey whose relying party
        the Entrance does not serve), ``LISTENER`` for the console on the remote listener,
        ``REDUCED`` for a remote login while the Entrance is reduced, or None.
    """
    now = deps.records.clock.now()
    if device is None or device.status is not DeviceStatus.APPROVED:
        return FailureReason.DEVICE
    if device.expires_at is not None and device.expires_at <= now:
        return FailureReason.DEVICE
    # ADR-0033: the console's sessions open only on the loopback listener.
    if device.loopback_bound and arrival.listener is Listener.REMOTE:
        return FailureReason.LISTENER
    if device.key_kind is KeyKind.PASSKEY and deps.ceremony.relying_party(device.rp_id) is None:
        return FailureReason.DEVICE
    # A reduced Entrance has no remote listener; a request that reaches one anyway is refused.
    if arrival.listener is Listener.REMOTE:
        mode = await deps.records.store.entrance_mode.get()
        if mode is EntranceMode.REDUCED:
            return FailureReason.REDUCED
    return None


async def _device(deps: AuthDeps, device_id: DeviceId) -> EnrolledDevice | None:
    """Read the device a request names; None when no record has that id."""
    try:
        # Latency: one local primary-key read.
        return await deps.records.store.get_device(device_id)
    except DeviceNotFoundError:
        return None


def passkey_request_options(deps: AuthDeps, device: EnrolledDevice, challenge: bytes) -> str | None:
    """Build the WebAuthn request options a passkey device answers a challenge with.

    Args:
        deps: The login dependencies (the relying parties).
        device: The device being challenged.
        challenge: The challenge's raw bytes.

    Returns:
        ``PublicKeyCredentialRequestOptionsJSON`` allowing only the device's own credential, or
        None for an Ed25519 device (or a passkey whose relying party is not served).
    """
    party = deps.ceremony.relying_party(device.rp_id)
    if device.key_kind is not KeyKind.PASSKEY or party is None or device.credential_id is None:
        return None
    return authentication_options(party, challenge, b64url_decode(device.credential_id))


async def _open(
    deps: AuthDeps,
    device: EnrolledDevice,
    proof: DeviceProof,
    verified: VerifiedProof,
    arrival: Arrival,
) -> OpenedSession:
    """Record the login on the device, consult the travel lock, and open the bound session."""
    records = deps.records
    travel = deps.guards.travel
    network = await travel.network(arrival) if travel is not None else arrival.network
    try:
        # Latency: one local transaction; the counter check is repeated against the row itself.
        await records.store.record_login(
            device.id, records.clock.now(), network, verified.sign_count
        )
    except PasskeyRejectedError:
        # The counter did not move forward against the row itself: a racing or cloned passkey.
        raise await refuse_proof(deps, arrival, FailureStep.LOGIN, device.id) from None
    except (DeviceStatusConflictError, DeviceNotFoundError):
        # Locked or revoked while its factors were being checked: that decision stands.
        failure = Failure(FailureReason.DEVICE, FailureStep.LOGIN, arrival, device.id)
        raise await refuse(deps, failure) from None
    await records.store.logins.clear_failures(device.id)
    needs_step_up = await travel.check(device, arrival, network) if travel is not None else False
    binding_kind, binding_key = _binding(device, proof)
    grant = SessionGrant(device, binding_kind, binding_key, arrival, network, needs_step_up)
    return await deps.sessions.open(grant)


def _binding(device: EnrolledDevice, proof: DeviceProof) -> tuple[BindingKind, str]:
    """Return the key a new session is bound to: the browser's P-256 key, or the device's own."""
    # A passkey device registered its browser's P-256 key with the challenge (binding_fits), and
    # the challenge was taken bound to that key, so it is the one every request must sign with.
    if device.key_kind is KeyKind.PASSKEY and proof.binding_key is not None:
        return BindingKind.P256, proof.binding_key
    # A program or the console: its own enrolled Ed25519 key signs every request.
    if device.key_kind is KeyKind.ED25519 and device.public_key is not None:
        return BindingKind.ED25519, device.public_key
    raise ValueError(f"Device {device.id} has no key its session could be bound to.")
