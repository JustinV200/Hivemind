"""Check the two factors of a login or a step-up: the device's key proof, then the password.

Login at the Hive Entrance (the Hive's one HTTP door) is the device key plus the operator's
password, the key proof first (ADR-0041). ``DeviceProof`` is what a device presents over the
challenge it was issued: an Ed25519 signature over ``login_string(hive_id, device_id, challenge)``
for a program or the console, or a WebAuthn assertion (user verification required, origin and
relying party checked, sign count moving forward) for a browser, which also names the WebCrypto
P-256 key its session will be bound to. ``verify_proof`` answers with the passkey's new sign count,
or None for a proof that does not hold; it never raises and never says which part failed.
``binding_fits`` checks, when the challenge is issued, that a browser registers a real P-256 point
and a program none. ``password_holds`` runs the operator password through the ``PasswordHasher``
(worker threads behind its semaphore) and is only ever reached after a proof held.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.login``. Called by
    the login flow and by step-up. Calls into ``hivemind.entrance.auth`` primitives (canonical,
    keys, passkeys, password) and the Entrance tables' operator row.

Key invariants:
    - A proof is checked against the kind of key the device enrolled, and only that kind.
    - Nothing here logs, stores or raises a signature, an assertion or a password.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Login is the device
      key plus the password, the key proof first".
    - hivemind.entrance.auth.canonical for ``login_string``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from hivemind.entrance.auth.canonical import b64url_decode, login_string
from hivemind.entrance.auth.challenges import Challenge
from hivemind.entrance.auth.keys import KeyKind, is_p256_point, verify_ed25519
from hivemind.entrance.auth.login.deps import LoginCeremony
from hivemind.entrance.auth.passkeys import StoredPasskey, verify_authentication
from hivemind.entrance.auth.password import PasswordHasher
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.errors import PasskeyRejectedError
from waggle.ids import DeviceId, HiveId

if TYPE_CHECKING:
    # Type-only: the store imports the enrolment models, so a runtime import would be a cycle.
    from hivemind.entrance.store.protocol import EntranceStore

__all__ = ["DeviceProof", "VerifiedProof", "binding_fits", "password_holds", "verify_proof"]


@dataclass(frozen=True, slots=True)
class DeviceProof:
    """What a device presents to prove it holds its key, over the challenge it was issued.

    Attributes:
        device_id: The device logging in (or stepping up).
        nonce: The challenge being answered, as issued.
        signature: An Ed25519 device's signature over ``login_string``, unpadded base64url.
        assertion: A passkey device's ``PublicKeyCredential.toJSON()`` assertion.
        binding_key: A browser's WebCrypto P-256 session key, registered with the challenge,
            unpadded base64url of the uncompressed point; None for an Ed25519 device.
    """

    device_id: DeviceId
    nonce: str
    signature: str | None = field(default=None, repr=False)
    assertion: str | None = field(default=None, repr=False)
    binding_key: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedProof:
    """A proof that held.

    Attributes:
        sign_count: A passkey's new signature counter, to store; None for an Ed25519 device.
    """

    sign_count: int | None


def verify_proof(
    ceremony: LoginCeremony,
    hive_id: HiveId,
    device: EnrolledDevice,
    proof: DeviceProof,
    challenge: Challenge,
) -> VerifiedProof | None:
    """Check ``proof`` against ``device``'s enrolled key over ``challenge``.

    Args:
        ceremony: The relying parties a passkey may be bound to.
        hive_id: The Hive being logged into; an Ed25519 signature names it.
        device: The device, as stored.
        proof: What it presented.
        challenge: The challenge it answers, already taken from the book.

    Returns:
        The verified proof, or None when it does not hold (never says why).
    """
    # The proof must be the kind of key the device enrolled; the other kind never counts.
    if device.key_kind is KeyKind.PASSKEY:
        return _passkey(ceremony, device, proof, challenge)
    return _ed25519(hive_id, device, proof, challenge)


def binding_fits(device: EnrolledDevice, binding_key: str | None) -> bool:
    """Return whether ``binding_key`` is the binding key ``device`` may register with a challenge.

    Args:
        device: The device asking for a login challenge.
        binding_key: The key it registers, or None.

    Returns:
        True for a passkey device registering a real P-256 point, or an Ed25519 device
        registering none (its own key binds its sessions); False otherwise.
    """
    if device.key_kind is not KeyKind.PASSKEY:
        return binding_key is None
    if binding_key is None:
        return False
    try:
        return is_p256_point(b64url_decode(binding_key))
    except ValueError:
        return False


async def password_holds(hasher: PasswordHasher, store: EntranceStore, password: str) -> bool:
    """Return whether ``password`` is the operator's.

    Args:
        hasher: The Entrance's hasher (worker threads, a semaphore of two).
        store: The Entrance tables, holding the operator's Argon2id hash.
        password: The password presented.

    Returns:
        True when it matches; False when it does not, or no operator exists yet.
    """
    # Latency: one local read, then about 0.1 s of Argon2id in a worker thread.
    operator = await store.get_operator()
    if operator is None:
        return False
    return await hasher.verify(password, operator.password_hash)


def _ed25519(
    hive_id: HiveId, device: EnrolledDevice, proof: DeviceProof, challenge: Challenge
) -> VerifiedProof | None:
    """Check an Ed25519 signature over ``login_string``; None when it does not hold."""
    if proof.signature is None or proof.assertion is not None or device.public_key is None:
        return None
    try:
        message = login_string(hive_id, device.id, challenge.nonce)
        signature = b64url_decode(proof.signature)
    except ValueError:
        return None
    holds = verify_ed25519(b64url_decode(device.public_key), message, signature)
    return VerifiedProof(sign_count=None) if holds else None


def _passkey(
    ceremony: LoginCeremony, device: EnrolledDevice, proof: DeviceProof, challenge: Challenge
) -> VerifiedProof | None:
    """Check a WebAuthn assertion with the passkey's relying party; None when it does not hold."""
    party = ceremony.relying_party(device.rp_id)
    if party is None or proof.assertion is None or proof.signature is not None:
        return None
    # The model guarantees a passkey device carries both; checked again to satisfy the types.
    if device.credential_id is None or device.public_key is None:
        return None
    stored = StoredPasskey(
        credential_id=b64url_decode(device.credential_id),
        public_key=b64url_decode(device.public_key),
        sign_count=device.sign_count,
    )
    try:
        sign_count = verify_authentication(
            party, challenge.challenge_bytes, proof.assertion, stored
        )
    except PasskeyRejectedError:
        return None
    return VerifiedProof(sign_count=sign_count)
