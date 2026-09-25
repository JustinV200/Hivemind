"""Run the Entrance's side of WebAuthn: passkey options out, registrations and assertions checked.

The Hive Entrance is the Hive's one HTTP door. A browser enrols with a passkey and logs in by
asserting it (ADR-0041). This module is a thin, fixed-policy wrapper over the ``webauthn`` library:
registration options for an invite (user verification REQUIRED, ``none`` attestation, a resident key
PREFERRED, EdDSA, ES256 and RS256), authentication options for one stored credential, the two
verifications, each refusing a ceremony without user verification and turning every library failure
into one ``PasskeyRejectedError``, and ``registration_challenge``, which reads the challenge a
registration claims to answer so the Entrance can find the one it issued before verifying. Two
platform rules shape where this can run at all: WebAuthn refuses an IP address as a relying
party, and browsers offer it only in a secure context. So the Hive Stand's (the Queen's machine)
own browser uses ``http://localhost`` (secure by definition, relying party ``localhost``) and a
remote browser uses an https DNS name (``public_url``'s host); ``RelyingParty`` refuses an IP
address outright. The relying party and its expected origins are arguments here: the Entrance
derives them from ``[entrance] bind`` and ``public_url`` (or ``rp_id``).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by the
    Entrance's enrolment (``hivemind.entrance.enrol.redeem``) and, later, login, and exercised
    end to end by ``hivemind.entrance.auth.fake.SoftPasskey``. Calls into ``webauthn`` only.

Key invariants:
    - Every verification requires user verification, checks the challenge, the relying party's id
      hash and the origin, and refuses a sign count that does not move forward (unless both the
      stored and the presented counts are 0, which many platform authenticators always report).
    - An assertion is accepted only for the stored credential it was checked against.
    - Every refusal is a ``PasskeyRejectedError``; no library exception escapes.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the decision.
    - hivemind.entrance.auth.fake for SoftPasskey, the software authenticator tests drive.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import parse_client_data_json, parse_registration_credential_json
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from hivemind.entrance.errors import PasskeyRejectedError

MIN_CHALLENGE_BYTES = 16  # WebAuthn's floor for a challenge; the Entrance issues 32.
REGISTRATION_TIMEOUT_MS = 120_000  # A first passkey setup can take a person a minute or two.
AUTHENTICATION_TIMEOUT_MS = 60_000  # ADR-0041: a login challenge lives 60 seconds.
# EdDSA, then ES256, then RS256: every algorithm a current platform authenticator offers, and
# nothing weaker; the same list gates which keys a registration may carry.
SUPPORTED_ALGORITHMS = (
    COSEAlgorithmIdentifier.EDDSA,
    COSEAlgorithmIdentifier.ECDSA_SHA_256,
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
)
# What malformed input can make the library raise besides its own WebAuthnException: a COSE key
# missing a label (KeyError), an empty key (IndexError), a bad curve point (ValueError), a field
# of the wrong type (TypeError), a signature check that escapes its own wrapper, and JSON nested
# deep enough to exhaust the parser's recursion (RecursionError), which an unauthenticated
# redeeming device can send.
_LIBRARY_FAILURES = (
    WebAuthnException,
    KeyError,
    IndexError,
    ValueError,
    TypeError,
    InvalidSignature,
    RecursionError,
)

__all__ = [
    "AUTHENTICATION_TIMEOUT_MS",
    "MIN_CHALLENGE_BYTES",
    "REGISTRATION_TIMEOUT_MS",
    "SUPPORTED_ALGORITHMS",
    "PasskeyRegistration",
    "RelyingParty",
    "StoredPasskey",
    "authentication_options",
    "registration_challenge",
    "registration_options",
    "verify_authentication",
    "verify_registration",
]


@dataclass(frozen=True, slots=True)
class RelyingParty:
    """The WebAuthn relying party a ceremony is for, and the origins it may come from.

    Attributes:
        id: The relying party id: ``localhost`` on the Hive Stand, or the remote DNS name. Never
            an IP address (WebAuthn refuses one).
        name: What the authenticator shows the person, e.g. ``"HiveMind"``.
        origins: Every exact origin a ceremony may come from, e.g. ``("http://localhost:8710",)``.
    """

    id: str
    name: str
    origins: tuple[str, ...]

    def __post_init__(self) -> None:
        """Refuse an empty id or name, an IP-address id, or no origins at all."""
        if not self.id or not self.name or not self.origins:
            raise ValueError("A relying party needs an id, a name and at least one origin.")
        # ip_address accepts both families; anything it parses is an address, not a name.
        try:
            ipaddress.ip_address(self.id)
        except ValueError:
            return
        raise ValueError(
            f"WebAuthn refuses an IP address as a relying party id ({self.id!r}); use "
            "'localhost' on the Hive Stand or the Entrance's DNS name remotely."
        )


@dataclass(frozen=True, slots=True)
class PasskeyRegistration:
    """What a verified registration yields, for the enrolled-device record.

    Attributes:
        credential_id: The credential's raw id.
        public_key: The credential public key as a COSE_Key, exactly as the library stores it.
        sign_count: The authenticator's signature counter at registration.
        rp_id: The relying party id the passkey is bound to.
        backup_eligible: The BE flag: the passkey may be synced to other devices.
        backup_state: The BS flag: the passkey is synced now.
    """

    credential_id: bytes
    public_key: bytes
    sign_count: int
    rp_id: str
    backup_eligible: bool
    backup_state: bool


@dataclass(frozen=True, slots=True)
class StoredPasskey:
    """The stored facts an assertion is checked against.

    Attributes:
        credential_id: The credential's raw id, as registered.
        public_key: Its COSE_Key, as registered.
        sign_count: The highest signature counter accepted so far.
    """

    credential_id: bytes
    public_key: bytes
    sign_count: int


def registration_options(
    relying_party: RelyingParty, challenge: bytes, user_handle: bytes, user_name: str
) -> str:
    """Build the options JSON a browser passes to ``navigator.credentials.create``.

    Args:
        relying_party: Where the passkey will be bound.
        challenge: The random challenge the Entrance issued for this invite (kept to verify).
        user_handle: The opaque WebAuthn user id, at most 64 bytes; the Entrance passes the
            device id, so two devices sharing one authenticator never replace each other.
        user_name: What the authenticator labels the passkey with (the operator's name).

    Returns:
        The ``PublicKeyCredentialCreationOptionsJSON`` document.

    Raises:
        ValueError: ``challenge`` is shorter than ``MIN_CHALLENGE_BYTES``.
    """
    _require_challenge(challenge)
    options = generate_registration_options(
        rp_id=relying_party.id,
        rp_name=relying_party.name,
        user_id=user_handle,
        user_name=user_name,
        challenge=challenge,
        timeout=REGISTRATION_TIMEOUT_MS,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        supported_pub_key_algs=list(SUPPORTED_ALGORITHMS),
    )
    return options_to_json(options)


def authentication_options(
    relying_party: RelyingParty, challenge: bytes, credential_id: bytes
) -> str:
    """Build the options JSON a browser passes to ``navigator.credentials.get``.

    Args:
        relying_party: Where the passkey is bound.
        challenge: The login challenge the Entrance issued.
        credential_id: The device's registered credential, the only one allowed to answer.

    Returns:
        The ``PublicKeyCredentialRequestOptionsJSON`` document.

    Raises:
        ValueError: ``challenge`` is shorter than ``MIN_CHALLENGE_BYTES``.
    """
    _require_challenge(challenge)
    options = generate_authentication_options(
        rp_id=relying_party.id,
        challenge=challenge,
        timeout=AUTHENTICATION_TIMEOUT_MS,
        allow_credentials=[PublicKeyCredentialDescriptor(id=credential_id)],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options_to_json(options)


def verify_registration(
    relying_party: RelyingParty, challenge: bytes, response_json: str
) -> PasskeyRegistration:
    """Verify a browser's registration response, with user verification required.

    Args:
        relying_party: The relying party the options were built for.
        challenge: The challenge those options carried.
        response_json: ``PublicKeyCredential.toJSON()`` of the new credential, as the browser
            sent it.

    Returns:
        The credential id, COSE public key, sign count, relying party id and backup flags.

    Raises:
        PasskeyRejectedError: The response is malformed, for another challenge, origin or relying
            party, lacks user verification, or uses an unsupported algorithm.
    """
    try:
        verified = verify_registration_response(
            credential=response_json,
            expected_challenge=challenge,
            expected_rp_id=relying_party.id,
            expected_origin=list(relying_party.origins),
            require_user_verification=True,
            supported_pub_key_algs=list(SUPPORTED_ALGORITHMS),
        )
    except _LIBRARY_FAILURES as exc:
        raise PasskeyRejectedError(
            f"The passkey registration for relying party {relying_party.id!r} was rejected: {exc}"
        ) from exc
    return PasskeyRegistration(
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        rp_id=relying_party.id,
        backup_eligible=verified.credential_device_type is CredentialDeviceType.MULTI_DEVICE,
        backup_state=verified.credential_backed_up,
    )


def registration_challenge(response_json: str) -> bytes:
    """Return the challenge a browser's registration response says it answers, unverified.

    Only a lookup key: the Entrance finds the challenge it issued under these bytes (the
    ``ChallengeBook``), then ``verify_registration`` checks the whole ceremony against it, so a
    response naming a challenge the Entrance never issued, or one issued for another invite, is
    refused before any verification runs.

    Args:
        response_json: ``PublicKeyCredential.toJSON()`` of the new credential, as sent.

    Returns:
        The challenge bytes from the response's ``clientDataJSON``.

    Raises:
        PasskeyRejectedError: The response or its client data is malformed.
    """
    try:
        credential = parse_registration_credential_json(response_json)
        return parse_client_data_json(credential.response.client_data_json).challenge
    except _LIBRARY_FAILURES as exc:
        raise PasskeyRejectedError(
            f"The passkey registration names no readable challenge: {exc}"
        ) from exc


def verify_authentication(
    relying_party: RelyingParty, challenge: bytes, response_json: str, stored: StoredPasskey
) -> int:
    """Verify a browser's assertion against the stored credential; return the new sign count.

    Args:
        relying_party: The relying party the passkey is bound to.
        challenge: The login challenge the options carried.
        response_json: ``PublicKeyCredential.toJSON()`` of the assertion.
        stored: The registered credential and the highest sign count accepted so far.

    Returns:
        The sign count to store for the next login.

    Raises:
        PasskeyRejectedError: The assertion is malformed, for another credential, challenge,
            origin or relying party, lacks user verification, fails its signature, or carries a
            sign count that did not move forward.
    """
    try:
        verified = verify_authentication_response(
            credential=response_json,
            expected_challenge=challenge,
            expected_rp_id=relying_party.id,
            expected_origin=list(relying_party.origins),
            credential_public_key=stored.public_key,
            credential_current_sign_count=stored.sign_count,
            require_user_verification=True,
        )
    except _LIBRARY_FAILURES as exc:
        raise PasskeyRejectedError(
            f"The passkey assertion for relying party {relying_party.id!r} was rejected: {exc}"
        ) from exc
    # The library checks the signature against the key it is given, not which credential the
    # response names; a device may only ever answer with its own credential.
    if verified.credential_id != stored.credential_id:
        raise PasskeyRejectedError(
            f"The passkey assertion for relying party {relying_party.id!r} names another "
            "credential than the device registered."
        )
    return verified.new_sign_count


def _require_challenge(challenge: bytes) -> None:
    """Refuse a challenge too short to be unguessable."""
    if len(challenge) < MIN_CHALLENGE_BYTES:
        raise ValueError(
            f"A WebAuthn challenge needs at least {MIN_CHALLENGE_BYTES} bytes, not "
            f"{len(challenge)}."
        )
