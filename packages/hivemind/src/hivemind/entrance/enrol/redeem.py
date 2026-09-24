"""Redeem an invite: a device presents the code with its own key and becomes a PENDING request.

A device holding an invite code opens the Entrance and proves it holds the key it wants to enrol
(ADR-0033). A program signs ``enrol_string(hive_id, sha256(code), public_key_hex)`` with its new
Ed25519 key (``redeem_ed25519``); a browser asks for passkey options (``passkey_options``, whose
challenge the ``ChallengeBook`` keeps bound to the invite) and answers them with a new passkey,
created with user verification (``redeem_passkey``). Either way the invite must exist, be unused
and unexpired, and its device still INVITED; the device then moves INVITED to PENDING with its key,
the backup flags, whether it is interactive (a passkey always; a program key only if the operator
later approves it so), its self-description and the request's expiry (``pending_ttl_hours``),
the invite is spent in the same atomic step, ``guard.entrance_pending`` is recorded and every other
device is told "a device is asking to join". The device gets back its id, its key's fingerprint
(to compare with what the operator sees) and the Hive's public key (to pin). The routes calling
this are unauthenticated and rate-limited per address, so **every** refusal raises the same
``EnrolmentRefusedError`` and records ``guard.entrance_redeem_failed`` with the requesting address
and a reason category, never the code and never the key: the device learns nothing, the Guard Bee
sees everything.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Called by the
    unauthenticated enrolment routes (a later step). Calls into ``hivemind.entrance.auth`` (the
    enrolment string, the key and passkey checks, the challenge book), the Entrance tables and the
    Pheromone Trail through ``EnrolmentDeps``, and ``hivemind.entrance.enrol.record``.

Key invariants:
    - An invite admits one device, once: two redemptions of one code can both pass the checks
      here, but only one wins ``EntranceStore.redeem_invite``; the other is refused.
    - Every refusal is one ``EnrolmentRefusedError`` with one message, raised after its
      ``guard.entrance_redeem_failed`` event is on the trail; no refusal chains its cause.
    - No trail payload, log line or message here carries the code, a key or a signature.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for redemption.
    - hivemind.entrance.enrol.invite for the code and its hash.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.canonical import b64url_decode, b64url_encode, enrol_string
from hivemind.entrance.auth.keys import KeyKind, key_fingerprint, verify_ed25519
from hivemind.entrance.auth.passkeys import (
    REGISTRATION_TIMEOUT_MS,
    PasskeyRegistration,
    registration_challenge,
    registration_options,
    verify_registration,
)
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.enrol.invite import invite_code_hash
from hivemind.entrance.enrol.models import DeviceDescription, DeviceInvite
from hivemind.entrance.enrol.record import notify
from hivemind.entrance.enrol.state import DeviceStatus, trail_kind
from hivemind.entrance.errors import (
    ChallengeRejectedError,
    DeviceStatusConflictError,
    EnrolmentRefusedError,
    InviteAlreadyUsedError,
    InviteExpiredError,
    InviteNotFoundError,
    PasskeyRejectedError,
)
from waggle.ids import DeviceId

if TYPE_CHECKING:
    # Type-only: hivemind.entrance.store imports this package's models (see deps.bundle).
    from hivemind.entrance.store.protocol import DeviceChanges

REDEEM_FAILED_KIND = "guard.entrance_redeem_failed"  # Every refusal's trail kind (no edge moves).
PASSKEY_USER_NAME = "operator"  # How a passkey is labelled in an authenticator: the one human.
# A passkey registration's challenge lives as long as the browser gives a person to set one up.
ENROLMENT_CHALLENGE_TTL = timedelta(milliseconds=REGISTRATION_TIMEOUT_MS)
MAX_ADDRESS_CHARS = 64  # An IPv6 address with its zone fits; anything longer is not an address.
UNREADABLE_ADDRESS = "unreadable"  # Recorded in place of an address that is not one.
# What a network address (or a test client's host name) looks like: nothing that could smuggle
# a line break or markup onto the trail.
_ADDRESS = re.compile(rf"[0-9A-Za-z.:%_\-\[\]]{{1,{MAX_ADDRESS_CHARS}}}")
# What the atomic redemption step can refuse after every check here passed: another redemption,
# a cancellation or the expiry sweep got there first, or the key material cannot form a record.
_LOST_RACE = (
    InviteAlreadyUsedError,
    InviteExpiredError,
    InviteNotFoundError,
    DeviceStatusConflictError,
    ValidationError,
)

log = get_logger(__name__)

__all__ = [
    "ENROLMENT_CHALLENGE_TTL",
    "MAX_ADDRESS_CHARS",
    "PASSKEY_USER_NAME",
    "REDEEM_FAILED_KIND",
    "UNREADABLE_ADDRESS",
    "Ed25519Proof",
    "RedeemFailure",
    "RedeemStep",
    "Redemption",
    "passkey_options",
    "redeem_ed25519",
    "redeem_passkey",
]


class RedeemFailure(Enum):
    """Why a redemption was refused: recorded on the trail, never told to the device."""

    UNKNOWN_CODE = "unknown_code"  # Not an invite code at all, or no invite has it.
    USED_CODE = "used_code"  # The invite was already redeemed (or lost a race to be).
    EXPIRED_CODE = "expired_code"  # The invite's lifetime passed.
    WRONG_STATE = "wrong_state"  # Its device is no longer INVITED: cancelled, or expired.
    BAD_PROOF = "bad_proof"  # The key proof or the passkey ceremony did not verify.


class RedeemStep(Enum):
    """Which request a refusal answered."""

    OPTIONS = "options"  # A browser asking for passkey options for a code.
    REDEEM = "redeem"  # A device presenting its key.


@dataclass(frozen=True, slots=True)
class Ed25519Proof:
    """What a program presents to show it holds the Ed25519 key it enrols.

    Attributes:
        public_key_hex: The raw 32-byte public key, 64 lowercase hex characters.
        signature: Its signature over ``enrol_string(hive_id, sha256(code), public_key_hex)``,
            the raw 64 bytes as unpadded base64url.
    """

    public_key_hex: str
    signature: str


@dataclass(frozen=True, slots=True)
class Redemption:
    """What a device learns once its request is waiting for the operator.

    Attributes:
        device_id: Its record, now PENDING.
        fingerprint: Its key's fingerprint, which the operator sees at approval too.
        hive_public_key_hex: The Hive's Ed25519 public key in hex, for a program to pin.
    """

    device_id: DeviceId
    fingerprint: str
    hive_public_key_hex: str


@dataclass(frozen=True, slots=True)
class _Attempt:
    """Who is trying and how, for a refusal's event: the address, the step, the key kind."""

    address: str
    step: RedeemStep
    key_kind: KeyKind


async def passkey_options(deps: EnrolmentDeps, code: str, address: str) -> str:
    """Issue the passkey creation options a browser answers to redeem ``code``.

    Args:
        deps: The enrolment dependencies.
        code: The invite code, as the device has it.
        address: The requesting network address, for the trail.

    Returns:
        ``PublicKeyCredentialCreationOptionsJSON``, its challenge bound to this invite.

    Raises:
        EnrolmentRefusedError: The invite cannot be redeemed (never says why).
    """
    attempt = _Attempt(_trail_address(address), RedeemStep.OPTIONS, KeyKind.PASSKEY)
    invite = await _live_invite(deps, code, attempt)
    # A fresh challenge per request (a reloaded page simply asks again); only a live code gets
    # one, and the book's cap bounds how many a code holder can leave open.
    challenge = deps.ceremony.challenges.issue(invite.code_hash)
    # The device id is the WebAuthn user handle, so two devices on one authenticator never
    # replace each other's passkey.
    return registration_options(
        deps.ceremony.relying_party,
        challenge.challenge_bytes,
        invite.device_id.encode("ascii"),
        PASSKEY_USER_NAME,
    )


async def redeem_ed25519(
    deps: EnrolmentDeps,
    code: str,
    proof: Ed25519Proof,
    description: DeviceDescription,
    address: str,
) -> Redemption:
    """Redeem ``code`` for a program holding an Ed25519 key: its request waits for the operator.

    Args:
        deps: The enrolment dependencies.
        code: The invite code, as the program has it.
        proof: The public key and its signature over the enrolment string.
        description: What the program says about itself.
        address: The requesting network address, for the trail.

    Returns:
        The device id, the key's fingerprint and the Hive's public key.

    Raises:
        EnrolmentRefusedError: The invite cannot be redeemed or the proof does not verify.
    """
    attempt = _Attempt(_trail_address(address), RedeemStep.REDEEM, KeyKind.ED25519)
    invite = await _live_invite(deps, code, attempt)
    # A proof that does not verify is refused like every other failure; only the trail says why.
    if not _ed25519_proof_holds(deps, invite, proof):
        raise await _refuse(deps, attempt, RedeemFailure.BAD_PROOF, invite.device_id)
    # A program key is not interactive until the operator approves it as such (ADR-0033).
    changes: DeviceChanges = {
        "key_kind": KeyKind.ED25519,
        "public_key": b64url_encode(bytes.fromhex(proof.public_key_hex)),
        "interactive": False,
    }
    return await _admit(deps, invite, attempt, changes, description)


async def redeem_passkey(
    deps: EnrolmentDeps,
    code: str,
    registration: str,
    description: DeviceDescription,
    address: str,
) -> Redemption:
    """Redeem ``code`` for a browser with a new passkey: its request waits for the operator.

    Args:
        deps: The enrolment dependencies.
        code: The invite code, from the link's fragment.
        registration: ``PublicKeyCredential.toJSON()`` of the new passkey, answering the options
            ``passkey_options`` issued for this code.
        description: What the browser says about itself.
        address: The requesting network address, for the trail.

    Returns:
        The device id, the passkey's fingerprint and the Hive's public key.

    Raises:
        EnrolmentRefusedError: The invite cannot be redeemed or the ceremony does not verify.
    """
    attempt = _Attempt(_trail_address(address), RedeemStep.REDEEM, KeyKind.PASSKEY)
    invite = await _live_invite(deps, code, attempt)
    try:
        verified = _verified_registration(deps, invite, registration)
    except (PasskeyRejectedError, ChallengeRejectedError):
        # WHY: from None; the cause would say which check failed, and the trail has it.
        raise await _refuse(deps, attempt, RedeemFailure.BAD_PROOF, invite.device_id) from None
    return await _admit(deps, invite, attempt, _passkey_changes(verified), description)


# ──────────────────────────────────────────────────────────────────────────────
# The checks every redemption shares
# ──────────────────────────────────────────────────────────────────────────────


async def _live_invite(deps: EnrolmentDeps, code: str, attempt: _Attempt) -> DeviceInvite:
    """Return the invite ``code`` names if it can still admit its device, else refuse."""
    # A malformed code and an unknown one are the same to the device: nothing to redeem.
    try:
        code_hash = invite_code_hash(code)
    except ValueError:
        raise await _refuse(deps, attempt, RedeemFailure.UNKNOWN_CODE) from None
    try:
        # Latency: one local primary-key read.
        invite = await deps.records.store.get_invite(code_hash)
    except InviteNotFoundError:
        raise await _refuse(deps, attempt, RedeemFailure.UNKNOWN_CODE) from None
    failure = await _invite_failure(deps, invite)
    if failure is not None:
        raise await _refuse(deps, attempt, failure, invite.device_id)
    return invite


async def _invite_failure(deps: EnrolmentDeps, invite: DeviceInvite) -> RedeemFailure | None:
    """Say why ``invite`` cannot admit its device right now, or None when it can."""
    if invite.used_at is not None:
        return RedeemFailure.USED_CODE
    if deps.records.clock.now() >= invite.expires_at:
        return RedeemFailure.EXPIRED_CODE
    # The operator may have cancelled it, or the sweep expired its device, since it was minted.
    device = await deps.records.store.get_device(invite.device_id)
    if device.status is not DeviceStatus.INVITED:
        return RedeemFailure.WRONG_STATE
    return None


def _ed25519_proof_holds(deps: EnrolmentDeps, invite: DeviceInvite, proof: Ed25519Proof) -> bool:
    """Return whether ``proof`` signs this Hive's enrolment string for this invite and key."""
    try:
        # enrol_string refuses anything but 64 lowercase hex characters for the key.
        message = enrol_string(
            deps.records.identity.hive_id, invite.code_hash, proof.public_key_hex
        )
        signature = b64url_decode(proof.signature)
    except ValueError:
        return False
    return verify_ed25519(bytes.fromhex(proof.public_key_hex), message, signature)


def _verified_registration(
    deps: EnrolmentDeps, invite: DeviceInvite, registration: str
) -> PasskeyRegistration:
    """Take the challenge the registration answers (it must be this invite's), then verify it."""
    claimed = registration_challenge(registration)
    challenge = deps.ceremony.challenges.take(b64url_encode(claimed), invite.code_hash)
    return verify_registration(deps.ceremony.relying_party, challenge.challenge_bytes, registration)


def _passkey_changes(verified: PasskeyRegistration) -> DeviceChanges:
    """Return the record fields a verified passkey sets; a passkey is always interactive."""
    return {
        "key_kind": KeyKind.PASSKEY,
        "public_key": b64url_encode(verified.public_key),
        "credential_id": b64url_encode(verified.credential_id),
        "sign_count": verified.sign_count,
        "rp_id": verified.rp_id,
        "backup_eligible": verified.backup_eligible,
        "backup_state": verified.backup_state,
        "interactive": True,
    }


async def _admit(
    deps: EnrolmentDeps,
    invite: DeviceInvite,
    attempt: _Attempt,
    changes: DeviceChanges,
    description: DeviceDescription,
) -> Redemption:
    """Spend the invite and move its device to PENDING with its event; refuse a lost race."""
    records = deps.records
    now = records.clock.now()
    changes["description"] = description
    changes["expires_at"] = now + deps.rules.pending_ttl
    fingerprint = key_fingerprint(b64url_decode(changes["public_key"]))
    payload: dict[str, JsonValue] = {
        "fingerprint": fingerprint,
        "key_kind": attempt.key_kind.value,
        "address": attempt.address,
    }
    # The device did this: it holds the code and proved its key, so it is the event's actor.
    kind = trail_kind(DeviceStatus.INVITED, DeviceStatus.PENDING)
    event = records.identity.event(records.clock, kind, invite.device_id, payload, invite.device_id)
    try:
        # Latency: one local transaction spending the invite, moving the device, writing the event.
        device = await records.store.redeem_invite(invite.code_hash, now, event, **changes)
    except _LOST_RACE as exc:
        raise await _refuse(deps, attempt, _race_failure(exc), invite.device_id) from None
    await notify(deps, device.id, event)
    return Redemption(device.id, fingerprint, deps.ceremony.hive_public_key.hex())


def _race_failure(error: Exception) -> RedeemFailure:
    """Name the reason category for an error the atomic redemption step raised."""
    if isinstance(error, InviteAlreadyUsedError):
        return RedeemFailure.USED_CODE
    if isinstance(error, InviteExpiredError):
        return RedeemFailure.EXPIRED_CODE
    if isinstance(error, InviteNotFoundError):
        return RedeemFailure.UNKNOWN_CODE
    if isinstance(error, DeviceStatusConflictError):
        return RedeemFailure.WRONG_STATE
    return RedeemFailure.BAD_PROOF


async def _refuse(
    deps: EnrolmentDeps,
    attempt: _Attempt,
    failure: RedeemFailure,
    device_id: DeviceId | None = None,
) -> EnrolmentRefusedError:
    """Record ``guard.entrance_redeem_failed`` and return the one generic refusal to raise."""
    records = deps.records
    # About the invite's device when the code named one, else about the Hive being knocked on.
    subject = device_id if device_id is not None else records.identity.hive_id
    payload: dict[str, JsonValue] = {
        "reason": failure.value,
        "step": attempt.step.value,
        "key_kind": attempt.key_kind.value,
        "address": attempt.address,
    }
    event = records.identity.event(records.clock, REDEEM_FAILED_KIND, subject, payload)
    # Latency: one local trail write, awaited so the refusal is on record before it is raised.
    await records.trail.record(event)
    log.info("entrance.redeem_refused", reason=failure.value, address=attempt.address)
    return EnrolmentRefusedError()


def _trail_address(address: str) -> str:
    """Return ``address`` if it looks like a network address, else a fixed placeholder."""
    return address if _ADDRESS.fullmatch(address) is not None else UNREADABLE_ADDRESS
