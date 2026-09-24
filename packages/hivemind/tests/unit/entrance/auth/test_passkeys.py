"""Tests for hivemind.entrance.auth.passkeys: WebAuthn options and verification, end to end.

Every ceremony is driven by SoftPasskey (hivemind.entrance.auth.fake) through the real
``webauthn`` verification, so each accepted case is the library's own acceptance and each refused
case is a tampered or wrong response it must refuse.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/passkeys.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.passkeys for the module under test.
"""

from __future__ import annotations

import json
import os

import pytest

from hivemind.entrance.auth.canonical import b64url_decode, b64url_encode
from hivemind.entrance.auth.fake import SoftPasskey
from hivemind.entrance.auth.passkeys import (
    MIN_CHALLENGE_BYTES,
    PasskeyRegistration,
    RelyingParty,
    StoredPasskey,
    authentication_options,
    registration_challenge,
    registration_options,
    verify_authentication,
    verify_registration,
)
from hivemind.entrance.errors import PasskeyRejectedError

_ORIGIN = "http://localhost:8710"
_RP = RelyingParty(id="localhost", name="HiveMind", origins=(_ORIGIN,))
_OTHER_RP = RelyingParty(id="hive.example.ts.net", name="HiveMind", origins=(_ORIGIN,))
_USER_HANDLE = b"device_01M221E4C10R4XDPNQNRX85AAA"


def _register(passkey: SoftPasskey, relying_party: RelyingParty = _RP) -> tuple[bytes, str]:
    """Return (challenge, the passkey's registration response) for ``relying_party``'s options."""
    challenge = os.urandom(32)
    options = registration_options(relying_party, challenge, _USER_HANDLE, "operator")
    return challenge, passkey.create(options)


def _enrolled(passkey: SoftPasskey) -> PasskeyRegistration:
    """Register ``passkey`` with _RP and return what verification yields."""
    challenge, response = _register(passkey)
    return verify_registration(_RP, challenge, response)


def _assert(passkey: SoftPasskey, registration: PasskeyRegistration) -> tuple[bytes, str]:
    """Return (challenge, the passkey's assertion) for a login against ``registration``."""
    challenge = os.urandom(32)
    return challenge, passkey.get(
        authentication_options(_RP, challenge, registration.credential_id)
    )


def _stored(registration: PasskeyRegistration, sign_count: int | None = None) -> StoredPasskey:
    """The stored facts for ``registration``, optionally with another sign count."""
    count = registration.sign_count if sign_count is None else sign_count
    return StoredPasskey(registration.credential_id, registration.public_key, count)


# ──────────────────────────────────────────────────────────────────────────────
# Options and the relying party
# ──────────────────────────────────────────────────────────────────────────────


def test_registration_options_fix_the_entrance_policy() -> None:
    challenge = os.urandom(32)

    options = json.loads(registration_options(_RP, challenge, _USER_HANDLE, "operator"))

    assert options["rp"] == {"id": "localhost", "name": "HiveMind"}
    assert b64url_decode(options["challenge"]) == challenge
    assert b64url_decode(options["user"]["id"]) == _USER_HANDLE
    assert options["attestation"] == "none"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    assert options["authenticatorSelection"]["residentKey"] == "preferred"
    assert [param["alg"] for param in options["pubKeyCredParams"]] == [-8, -7, -257]


def test_authentication_options_allow_only_the_devices_credential() -> None:
    options = json.loads(authentication_options(_RP, os.urandom(32), b"\x01" * 16))

    assert options["rpId"] == "localhost"
    assert options["userVerification"] == "required"
    assert [entry["id"] for entry in options["allowCredentials"]] == [b64url_encode(b"\x01" * 16)]


def test_options_refuse_a_challenge_too_short_to_be_unguessable() -> None:
    short = b"\x00" * (MIN_CHALLENGE_BYTES - 1)

    with pytest.raises(ValueError, match="at least 16"):
        registration_options(_RP, short, _USER_HANDLE, "operator")
    with pytest.raises(ValueError, match="at least 16"):
        authentication_options(_RP, short, b"\x01")


@pytest.mark.parametrize("rp_id", ["127.0.0.1", "::1", "100.64.0.7"])
def test_a_relying_party_is_never_an_ip_address(rp_id: str) -> None:
    with pytest.raises(ValueError, match="IP address"):
        RelyingParty(id=rp_id, name="HiveMind", origins=(f"https://{rp_id}",))


def test_a_relying_party_needs_an_id_a_name_and_an_origin() -> None:
    with pytest.raises(ValueError, match="at least one origin"):
        RelyingParty(id="localhost", name="HiveMind", origins=())


# ──────────────────────────────────────────────────────────────────────────────
# Registration
# ──────────────────────────────────────────────────────────────────────────────


def test_a_registration_from_the_right_origin_is_accepted() -> None:
    passkey = SoftPasskey(_ORIGIN)

    registration = _enrolled(passkey)

    assert registration.credential_id == passkey.credential_id
    assert registration.rp_id == "localhost"
    assert registration.sign_count == 0
    assert (registration.backup_eligible, registration.backup_state) == (False, False)


def test_a_synced_passkey_reports_its_backup_flags() -> None:
    registration = _enrolled(SoftPasskey(_ORIGIN, backed_up=True))

    assert (registration.backup_eligible, registration.backup_state) == (True, True)


@pytest.mark.parametrize(
    ("passkey", "made_for", "why"),
    [
        (SoftPasskey("https://evil.example"), _RP, "origin"),
        (SoftPasskey(_ORIGIN), _OTHER_RP, "RP ID"),
        (SoftPasskey(_ORIGIN, user_verified=False), _RP, "User verification"),
    ],
)
def test_a_registration_is_refused_from_another_origin_rp_or_without_verification(
    passkey: SoftPasskey, made_for: RelyingParty, why: str
) -> None:
    challenge, response = _register(passkey, made_for)

    with pytest.raises(PasskeyRejectedError, match=why):
        verify_registration(_RP, challenge, response)


def test_a_registration_is_refused_for_another_challenge_or_as_garbage() -> None:
    _, response = _register(SoftPasskey(_ORIGIN))

    with pytest.raises(PasskeyRejectedError, match="challenge"):
        verify_registration(_RP, os.urandom(32), response)
    with pytest.raises(PasskeyRejectedError):
        verify_registration(_RP, os.urandom(32), "{not json")


def test_a_registration_names_the_challenge_it_answers() -> None:
    challenge, response = _register(SoftPasskey(_ORIGIN))

    assert registration_challenge(response) == challenge


@pytest.mark.parametrize(
    "response",
    [
        "not json",
        "[]",
        json.dumps({"id": "x", "rawId": "x", "response": {}}),
        "[" * 100_000,  # Nested deep enough to exhaust the JSON parser's recursion.
    ],
)
def test_a_registration_naming_no_readable_challenge_is_refused(response: str) -> None:
    with pytest.raises(PasskeyRejectedError, match="no readable challenge"):
        registration_challenge(response)


def test_a_registration_whose_client_data_is_not_json_is_refused() -> None:
    _, response = _register(SoftPasskey(_ORIGIN))
    credential = json.loads(response)
    credential["response"]["clientDataJSON"] = b64url_encode(b"not json")

    with pytest.raises(PasskeyRejectedError):
        registration_challenge(json.dumps(credential))


# ──────────────────────────────────────────────────────────────────────────────
# Authentication
# ──────────────────────────────────────────────────────────────────────────────


def test_an_assertion_is_accepted_and_moves_the_sign_count_forward() -> None:
    passkey = SoftPasskey(_ORIGIN)
    registration = _enrolled(passkey)
    challenge, response = _assert(passkey, registration)

    assert verify_authentication(_RP, challenge, response, _stored(registration)) == 1


def test_an_authenticator_that_always_reports_zero_is_accepted() -> None:
    passkey = SoftPasskey(_ORIGIN, counter_step=0)
    registration = _enrolled(passkey)
    challenge, response = _assert(passkey, registration)

    assert verify_authentication(_RP, challenge, response, _stored(registration)) == 0


def test_a_sign_count_that_went_backwards_or_stood_still_is_refused() -> None:
    passkey = SoftPasskey(_ORIGIN)
    registration = _enrolled(passkey)
    challenge, response = _assert(passkey, registration)

    with pytest.raises(PasskeyRejectedError, match="sign count"):
        verify_authentication(_RP, challenge, response, _stored(registration, sign_count=5))
    with pytest.raises(PasskeyRejectedError, match="sign count"):
        verify_authentication(_RP, challenge, response, _stored(registration, sign_count=1))


def test_an_assertion_for_another_challenge_rp_or_origin_is_refused() -> None:
    passkey = SoftPasskey(_ORIGIN)
    registration = _enrolled(passkey)
    challenge, response = _assert(passkey, registration)
    elsewhere = RelyingParty(id="localhost", name="HiveMind", origins=("http://localhost:9999",))

    with pytest.raises(PasskeyRejectedError, match="challenge"):
        verify_authentication(_RP, os.urandom(32), response, _stored(registration))
    with pytest.raises(PasskeyRejectedError, match="RP ID"):
        verify_authentication(_OTHER_RP, challenge, response, _stored(registration))
    with pytest.raises(PasskeyRejectedError, match="origin"):
        verify_authentication(elsewhere, challenge, response, _stored(registration))


def test_an_assertion_without_user_verification_is_refused() -> None:
    passkey = SoftPasskey(_ORIGIN)
    registration = _enrolled(passkey)
    passkey.user_verified = False  # The same authenticator, this time only touched.
    challenge, response = _assert(passkey, registration)

    with pytest.raises(PasskeyRejectedError, match="User verification"):
        verify_authentication(_RP, challenge, response, _stored(registration))


def test_a_tampered_signature_is_refused() -> None:
    passkey = SoftPasskey(_ORIGIN)
    registration = _enrolled(passkey)
    challenge, response = _assert(passkey, registration)
    credential = json.loads(response)
    signature = b64url_decode(credential["response"]["signature"])
    credential["response"]["signature"] = b64url_encode(signature[:-1] + b"\x00")

    with pytest.raises(PasskeyRejectedError, match="signature"):
        verify_authentication(_RP, challenge, json.dumps(credential), _stored(registration))


def test_an_assertion_naming_another_credential_is_refused() -> None:
    passkey = SoftPasskey(_ORIGIN)
    registration = _enrolled(passkey)
    challenge, response = _assert(passkey, registration)
    stored = StoredPasskey(b"\x09" * 32, registration.public_key, registration.sign_count)

    with pytest.raises(PasskeyRejectedError, match="another credential"):
        verify_authentication(_RP, challenge, response, stored)
