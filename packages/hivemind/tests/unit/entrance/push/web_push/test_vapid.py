"""Tests for hivemind.entrance.push.web_push.vapid: the VAPID key, its loading, the RFC 8292 header.

Fits into the Hive:
    Mirrors src/hivemind/entrance/push/web_push/vapid.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.push.web_push.vapid for the module under test.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from pydantic import SecretStr
from unit.entrance.push.support import PUSH_URL, SteppingClock

from hivemind.common.secrets import MemorySecretStore
from hivemind.entrance.auth import b64url_decode, b64url_encode
from hivemind.entrance.push import PushConfigError
from hivemind.entrance.push.web_push import (
    VAPID_JWT_TTL_S,
    VAPID_KEY_NAME,
    VapidKey,
    VapidSigner,
    load_or_mint_vapid_key,
    vapid_audience,
)
from hivemind.manifest import read_env

_SUBJECT = "mailto:operator@example.net"  # The contact a push service may write to.
_MAX_EXPIRY = timedelta(hours=24)  # RFC 8292 section 2: exp at most 24 hours out.


def _parse(header: str) -> tuple[dict[str, object], dict[str, object], bytes, bytes, bytes]:
    """Split ``vapid t=<jwt>, k=<key>`` into header, claims, signing input, signature and key."""
    scheme, _, rest = header.partition(" ")
    assert scheme == "vapid"
    token_part, key_part = rest.split(", ")
    assert token_part.startswith("t=") and key_part.startswith("k=")
    encoded_header, encoded_claims, encoded_signature = token_part[2:].split(".")
    return (
        json.loads(b64url_decode(encoded_header)),
        json.loads(b64url_decode(encoded_claims)),
        f"{encoded_header}.{encoded_claims}".encode("ascii"),
        b64url_decode(encoded_signature),
        b64url_decode(key_part[2:]),
    )


def _verify_es256(public_key: bytes, signing_input: bytes, signature: bytes) -> None:
    """Verify a JOSE ES256 signature (raw r || s) with a raw uncompressed P-256 public key."""
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
    r = int.from_bytes(signature[:32], "big")
    s = int.from_bytes(signature[32:], "big")
    key.verify(encode_dss_signature(r, s), signing_input, ec.ECDSA(hashes.SHA256()))


def test_authorization_carries_a_jwt_that_verifies_with_the_public_key() -> None:
    clock = SteppingClock()
    key = VapidKey.generate()
    signer = VapidSigner(key, _SUBJECT, clock)

    jwt_header, _, signing_input, signature, public_key = _parse(signer.authorization(PUSH_URL))

    assert jwt_header == {"alg": "ES256", "typ": "JWT"}
    assert public_key == key.public_key_bytes
    assert len(signature) == 64
    _verify_es256(public_key, signing_input, signature)


def test_authorization_claims_the_origin_an_expiry_within_a_day_and_the_subject() -> None:
    clock = SteppingClock()
    signer = VapidSigner(VapidKey.generate(), _SUBJECT, clock)

    _, claims, _, _, _ = _parse(signer.authorization(PUSH_URL))

    now = int(clock.now().timestamp())
    assert claims["aud"] == "https://push.example.net"
    assert claims["sub"] == _SUBJECT
    assert claims["exp"] == now + VAPID_JWT_TTL_S
    assert now < now + VAPID_JWT_TTL_S <= now + _MAX_EXPIRY.total_seconds()


def test_authorization_signature_fails_for_another_key() -> None:
    signer = VapidSigner(VapidKey.generate(), _SUBJECT, SteppingClock())
    _, _, signing_input, signature, _ = _parse(signer.authorization(PUSH_URL))

    with pytest.raises(InvalidSignature):
        _verify_es256(VapidKey.generate().public_key_bytes, signing_input, signature)


def test_application_server_key_is_the_base64url_uncompressed_point() -> None:
    key = VapidKey.generate()

    signer = VapidSigner(key, _SUBJECT, SteppingClock())

    assert b64url_decode(signer.application_server_key) == key.public_key_bytes
    assert key.public_key_bytes[0] == 0x04
    assert len(key.public_key_bytes) == 65


@pytest.mark.parametrize(
    ("endpoint", "origin"),
    [
        ("https://push.example.net/wpush/v2/abc?x=1", "https://push.example.net"),
        ("https://push.example.net:443/abc", "https://push.example.net"),
        ("https://push.example.net:8443/abc", "https://push.example.net:8443"),
    ],
)
def test_vapid_audience_is_the_endpoint_origin(endpoint: str, origin: str) -> None:
    assert vapid_audience(endpoint) == origin


@pytest.mark.parametrize(
    "subject", ["", "operator@example.net", "http://example.net", "mailto:a b@example.net"]
)
def test_signer_refuses_a_subject_that_is_not_a_mailto_or_https_uri(subject: str) -> None:
    with pytest.raises(PushConfigError, match="HIVEMIND_ENTRANCE_VAPID_SUBJECT"):
        VapidSigner(VapidKey.generate(), subject, SteppingClock())


def test_from_scalar_round_trips_the_private_scalar() -> None:
    key = VapidKey.generate()

    reloaded = VapidKey.from_scalar(key.private_scalar)

    assert reloaded.public_key_bytes == key.public_key_bytes
    assert len(key.private_scalar) == 32


@pytest.mark.parametrize("scalar", [b"", bytes(31), bytes(32), b"\xff" * 32])
def test_from_scalar_rejects_what_is_not_a_p256_scalar(scalar: bytes) -> None:
    with pytest.raises(ValueError):
        VapidKey.from_scalar(scalar)


def test_repr_shows_only_the_public_key() -> None:
    key = VapidKey.generate()

    text = repr(key)

    assert key.application_server_key[:16] in text
    assert b64url_encode(key.private_scalar) not in text


async def test_load_or_mint_mints_once_and_persists_the_scalar() -> None:
    store = MemorySecretStore()

    first = await load_or_mint_vapid_key(store)
    second = await load_or_mint_vapid_key(store)

    assert await store.get(VAPID_KEY_NAME) == first.private_scalar
    assert second.public_key_bytes == first.public_key_bytes


async def test_load_or_mint_prefers_the_environment_variable_read_by_read_env() -> None:
    # HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY, read the one way the Hive reads its environment.
    store = MemorySecretStore()
    await load_or_mint_vapid_key(store)
    pinned = VapidKey.generate()
    overrides = read_env(
        {"HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY": b64url_encode(pinned.private_scalar)}
    )

    key = await load_or_mint_vapid_key(store, overrides.entrance_vapid_private_key)

    assert key.public_key_bytes == pinned.public_key_bytes
    assert b64url_encode(pinned.private_scalar) not in repr(overrides)


async def test_load_or_mint_accepts_a_padded_environment_value() -> None:
    pinned = VapidKey.generate()
    padded = SecretStr(b64url_encode(pinned.private_scalar) + "=")

    key = await load_or_mint_vapid_key(MemorySecretStore(), padded)

    assert key.public_key_bytes == pinned.public_key_bytes


async def test_load_or_mint_refuses_a_malformed_environment_value_naming_only_the_variable() -> (
    None
):
    configured = "not-a-key"

    with pytest.raises(PushConfigError) as caught:
        await load_or_mint_vapid_key(MemorySecretStore(), SecretStr(configured))

    assert "HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY" in str(caught.value)
    assert configured not in str(caught.value)


async def test_load_or_mint_refuses_a_malformed_stored_key_instead_of_replacing_it() -> None:
    store = MemorySecretStore()
    await store.put(VAPID_KEY_NAME, b"\x01" * 7)

    with pytest.raises(PushConfigError, match=r"entrance\.vapid"):
        await load_or_mint_vapid_key(store)

    assert await store.get(VAPID_KEY_NAME) == b"\x01" * 7


def test_read_env_leaves_the_vapid_values_unset_when_absent() -> None:
    overrides = read_env({})

    assert overrides.entrance_vapid_private_key is None
    assert overrides.entrance_vapid_subject is None


def test_read_env_reads_the_vapid_subject() -> None:
    overrides = read_env({"HIVEMIND_ENTRANCE_VAPID_SUBJECT": _SUBJECT})

    assert overrides.entrance_vapid_subject == _SUBJECT
