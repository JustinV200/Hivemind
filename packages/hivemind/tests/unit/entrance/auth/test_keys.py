"""Tests for hivemind.entrance.auth.keys: key kinds, the two verifiers, the key fingerprint.

Fits into the Hive:
    Mirrors src/hivemind/entrance/auth/keys.py (codingrules section 3).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.entrance.auth.keys for the module under test.
"""

from __future__ import annotations

import base64
import re

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from hivemind.entrance.auth.keys import (
    KeyKind,
    key_fingerprint,
    verify_ed25519,
    verify_p256,
)
from waggle.signing import Ed25519Signer

_MESSAGE = b"hive-request-v1\nGET\n/v1/goals\n1790000000\nnonce\ndigest"
_FINGERPRINT = re.compile(r"[A-Z2-7]{4}(-[A-Z2-7]{4}){3}")


def _ed25519() -> tuple[bytes, bytes]:
    """Return a fresh (public key, signature over _MESSAGE) pair, both raw."""
    signer = Ed25519Signer.generate()
    return signer.public_key_bytes, base64.b64decode(signer.sign(_MESSAGE))


def _webcrypto_p256(message: bytes = _MESSAGE) -> tuple[bytes, bytes]:
    """Sign like WebCrypto: an uncompressed public point and an IEEE P1363 r||s signature."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    r, s = decode_dss_signature(private_key.sign(message, ec.ECDSA(hashes.SHA256())))
    return public_key, r.to_bytes(32, "big") + s.to_bytes(32, "big")


def test_key_kind_values_are_the_stored_strings() -> None:
    assert KeyKind.ED25519.value == "ed25519"
    assert KeyKind.PASSKEY.value == "passkey"


def test_verify_ed25519_accepts_a_valid_signature() -> None:
    public_key, signature = _ed25519()

    assert verify_ed25519(public_key, _MESSAGE, signature) is True


def test_verify_ed25519_rejects_another_message_or_another_key() -> None:
    public_key, signature = _ed25519()
    other_key, _ = _ed25519()

    assert verify_ed25519(public_key, _MESSAGE + b"x", signature) is False
    assert verify_ed25519(other_key, _MESSAGE, signature) is False


@pytest.mark.parametrize(
    ("key_length", "signature_length"), [(31, 64), (33, 64), (32, 63), (32, 0)]
)
def test_verify_ed25519_returns_false_for_malformed_lengths(
    key_length: int, signature_length: int
) -> None:
    assert verify_ed25519(b"\x01" * key_length, _MESSAGE, b"\x02" * signature_length) is False


def test_verify_p256_accepts_a_webcrypto_signature() -> None:
    public_key, signature = _webcrypto_p256()

    assert verify_p256(public_key, _MESSAGE, signature) is True


def test_verify_p256_rejects_another_message_or_another_key() -> None:
    public_key, signature = _webcrypto_p256()
    other_key, _ = _webcrypto_p256()

    assert verify_p256(public_key, _MESSAGE + b"x", signature) is False
    assert verify_p256(other_key, _MESSAGE, signature) is False


def test_verify_p256_rejects_a_der_signature_it_was_not_given_in_p1363_form() -> None:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    der = private_key.sign(_MESSAGE, ec.ECDSA(hashes.SHA256()))

    assert verify_p256(public_key, _MESSAGE, der) is False


def test_verify_p256_returns_false_for_malformed_keys_and_signatures() -> None:
    public_key, signature = _webcrypto_p256()
    compressed = b"\x02" + public_key[1:33]
    off_curve = b"\x04" + b"\x01" * 64
    wrong_prefix = b"\x05" + public_key[1:]
    zero_signature = bytes(64)

    assert verify_p256(compressed, _MESSAGE, signature) is False
    assert verify_p256(off_curve, _MESSAGE, signature) is False
    assert verify_p256(wrong_prefix, _MESSAGE, signature) is False
    assert verify_p256(public_key, _MESSAGE, signature[:63]) is False
    assert verify_p256(public_key, _MESSAGE, zero_signature) is False


def test_key_fingerprint_is_four_groups_of_base32_and_deterministic() -> None:
    public_key, _ = _ed25519()

    fingerprint = key_fingerprint(public_key)

    assert _FINGERPRINT.fullmatch(fingerprint)
    assert key_fingerprint(public_key) == fingerprint
    assert key_fingerprint(public_key + b"\x00") != fingerprint


def test_key_fingerprint_vector() -> None:
    # The first 10 bytes of SHA-256 of 32 zero bytes, in base32: a vector clients can check.
    assert key_fingerprint(bytes(32)) == "MZUH-VLPY-MK6X-O3EP"
