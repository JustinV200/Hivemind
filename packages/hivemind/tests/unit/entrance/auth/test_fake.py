"""Tests for hivemind.entrance.auth.fake: SoftPasskey behaves like a browser's authenticator.

Its outputs are checked against the real verifier in test_passkeys.py; this module checks what
the fake itself promises: the browser JSON shape, the counter, and refusing to answer for a
relying party or credential it does not hold.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/fake.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.fake for the module under test.
"""

from __future__ import annotations

import json
import os

import pytest

from hivemind.entrance.auth.canonical import b64url_decode, b64url_encode
from hivemind.entrance.auth.fake import CREDENTIAL_ID_BYTES, SoftPasskey
from hivemind.entrance.auth.passkeys import (
    RelyingParty,
    authentication_options,
    registration_options,
)

_ORIGIN = "http://localhost:8710"
_RP = RelyingParty(id="localhost", name="HiveMind", origins=(_ORIGIN,))


def _created(passkey: SoftPasskey) -> dict[str, object]:
    """Register ``passkey`` with _RP and return its parsed response JSON."""
    options = registration_options(_RP, os.urandom(32), b"device", "operator")
    parsed: dict[str, object] = json.loads(passkey.create(options))
    return parsed


def test_create_answers_in_the_json_shape_a_browser_sends() -> None:
    passkey = SoftPasskey(_ORIGIN)

    credential = _created(passkey)

    assert len(passkey.credential_id) == CREDENTIAL_ID_BYTES
    assert credential["id"] == credential["rawId"] == b64url_encode(passkey.credential_id)
    assert credential["type"] == "public-key"
    response = credential["response"]
    assert isinstance(response, dict)
    assert set(response) == {"clientDataJSON", "attestationObject", "transports"}
    client_data = json.loads(b64url_decode(response["clientDataJSON"]))
    assert client_data["type"] == "webauthn.create"
    assert client_data["origin"] == _ORIGIN


def test_each_get_advances_the_counter_by_its_step() -> None:
    passkey = SoftPasskey(_ORIGIN, counter_step=3)
    _created(passkey)
    options = authentication_options(_RP, os.urandom(32), passkey.credential_id)

    passkey.get(options)
    passkey.get(options)

    assert passkey.sign_count == 6


def test_get_carries_the_user_handle_from_registration() -> None:
    passkey = SoftPasskey(_ORIGIN)
    _created(passkey)

    assertion = json.loads(
        passkey.get(authentication_options(_RP, os.urandom(32), passkey.credential_id))
    )

    assert b64url_decode(assertion["response"]["userHandle"]) == b"device"


def test_get_refuses_before_any_credential_exists() -> None:
    with pytest.raises(ValueError, match="no credential for that relying party"):
        SoftPasskey(_ORIGIN).get(authentication_options(_RP, os.urandom(32), b"\x01" * 16))


def test_get_refuses_another_relying_party_or_another_credential() -> None:
    passkey = SoftPasskey(_ORIGIN)
    _created(passkey)
    elsewhere = RelyingParty(id="hive.example.ts.net", name="HiveMind", origins=(_ORIGIN,))

    with pytest.raises(ValueError, match="no credential for that relying party"):
        passkey.get(authentication_options(elsewhere, os.urandom(32), passkey.credential_id))
    with pytest.raises(ValueError, match="credentials this authenticator lacks"):
        passkey.get(authentication_options(_RP, os.urandom(32), b"\x01" * 16))
