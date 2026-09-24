"""Name the kinds of device key, verify their signatures, and fingerprint them for humans.

Every client of the Hive Entrance (the Hive's one HTTP door) is a device holding its own key
(ADR-0033): a program or the CLI holds an Ed25519 key; a browser holds a WebAuthn passkey for login
and, per session, a non-extractable WebCrypto ECDSA P-256 key that signs every request (the
session's binding key). This module holds the pure checks for the two raw signature schemes:
``verify_ed25519`` and ``verify_p256`` answer yes or no and never raise, because a malformed key or
signature from the network is a failed proof, not an error the caller should have to anticipate. The
passkey itself is checked by ``hivemind.entrance.auth.passkeys`` through the ``webauthn`` library.
``key_fingerprint`` is the short, grouped digest a device shows after it redeems an invite and every
approval surface shows beside it, so the operator can see the two match.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by the
    Entrance's enrolment, login and request verification (messages from
    ``hivemind.entrance.auth.canonical``), and by ``hivemind.entrance.enrol.models`` for
    ``KeyKind`` and the fingerprint. Calls into ``cryptography`` only.

Key invariants:
    - ``verify_ed25519`` and ``verify_p256`` return True only for a valid signature by exactly
      the given public key over exactly the given message; every other input returns False.
    - ``key_fingerprint`` is 16 base32 characters in four dash-separated groups, derived from the
      first 10 bytes of the key's SHA-256; the same key always gives the same fingerprint.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for which key signs
      what.
    - hivemind.entrance.auth.canonical for the signed strings and the wire encodings.
"""

from __future__ import annotations

import base64
import hashlib
from enum import Enum

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

ED25519_PUBLIC_KEY_BYTES = 32  # RFC 8032's public key, raw.
ED25519_SIGNATURE_BYTES = 64  # RFC 8032's signature, raw R || S.
P256_PUBLIC_KEY_BYTES = 65  # An uncompressed SEC1 point: 0x04 || X || Y, as WebCrypto exports it.
P256_SIGNATURE_BYTES = 64  # IEEE P1363 r || s, 32 bytes each: WebCrypto's ECDSA output.
_P256_COORDINATE_BYTES = 32  # One of r or s, big-endian.
_UNCOMPRESSED_POINT_PREFIX = 0x04  # SEC1's marker for an uncompressed point.
FINGERPRINT_DIGEST_BYTES = 10  # 80 bits: 16 base32 characters, no padding, easy to read aloud.
FINGERPRINT_GROUP_CHARS = 4  # "ABCD-EFGH-IJKL-MNOP": four groups a human compares at a glance.

__all__ = [
    "ED25519_PUBLIC_KEY_BYTES",
    "ED25519_SIGNATURE_BYTES",
    "FINGERPRINT_DIGEST_BYTES",
    "FINGERPRINT_GROUP_CHARS",
    "P256_PUBLIC_KEY_BYTES",
    "P256_SIGNATURE_BYTES",
    "KeyKind",
    "key_fingerprint",
    "verify_ed25519",
    "verify_p256",
]


class KeyKind(Enum):
    """Which kind of key an enrolled device logs in with."""

    ED25519 = "ed25519"  # A program or the CLI: an Ed25519 key in the device's secure storage.
    PASSKEY = "passkey"  # A browser: a WebAuthn passkey created with user verification required.


def verify_ed25519(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Return whether ``signature`` is a valid Ed25519 signature by ``public_key`` over ``message``.

    Args:
        public_key: The raw 32-byte public key the device enrolled.
        message: The exact bytes that were signed (a ``hivemind.entrance.auth.canonical`` string).
        signature: The raw 64-byte signature, already decoded from the wire's base64url.

    Returns:
        True for a valid signature; False for a wrong one, or a key or signature of the wrong
        length or shape. Never raises.
    """
    # Length checks first: cryptography would raise on a wrong-length key, and a wrong-length
    # signature can never verify, so both are simply a failed proof.
    if len(public_key) != ED25519_PUBLIC_KEY_BYTES or len(signature) != ED25519_SIGNATURE_BYTES:
        return False
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError):
        return False
    return True


def verify_p256(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Return whether ``signature`` is WebCrypto's ECDSA P-256/SHA-256 signature over ``message``.

    WebCrypto exports the public key as an uncompressed SEC1 point and signs in IEEE P1363 form
    (``r || s``, fixed width); ``cryptography`` verifies DER, so ``r`` and ``s`` are re-encoded
    with ``encode_dss_signature`` first.

    Args:
        public_key: The 65-byte uncompressed point the browser registered for its session.
        message: The exact bytes that were signed.
        signature: The 64-byte ``r || s`` signature, already decoded from base64url.

    Returns:
        True for a valid signature; False for a wrong one, a point not on the curve, or a key or
        signature of the wrong length or shape. Never raises.
    """
    # Only the uncompressed form WebCrypto produces is accepted, so one key has one encoding.
    if (
        len(public_key) != P256_PUBLIC_KEY_BYTES
        or public_key[0] != _UNCOMPRESSED_POINT_PREFIX
        or len(signature) != P256_SIGNATURE_BYTES
    ):
        return False
    r = int.from_bytes(signature[:_P256_COORDINATE_BYTES], "big")
    s = int.from_bytes(signature[_P256_COORDINATE_BYTES:], "big")
    try:
        # from_encoded_point raises ValueError for a point that is not on the curve.
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key)
        key.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        return False
    return True


def key_fingerprint(public_key: bytes) -> str:
    """Return the short fingerprint a device and every approval surface show for a public key.

    Computed over the public key exactly as the Entrance stores it: the raw 32 bytes of an
    Ed25519 key (a program computes it itself), or the COSE key of a passkey (the Entrance
    returns it to the page after redemption).

    Args:
        public_key: The public key bytes.

    Returns:
        The first ``FINGERPRINT_DIGEST_BYTES`` bytes of the key's SHA-256, in RFC 4648 base32
        without padding, grouped in fours with dashes, e.g. ``"MFRG-GZDF-MZTW-Q2LK"``.
    """
    digest = hashlib.sha256(public_key).digest()[:FINGERPRINT_DIGEST_BYTES]
    # 10 bytes is exactly 16 base32 characters, so there is no padding to strip; rstrip keeps
    # the output right even if the digest length is ever changed to one that pads.
    text = base64.b32encode(digest).decode("ascii").rstrip("=")
    groups = [
        text[start : start + FINGERPRINT_GROUP_CHARS]
        for start in range(0, len(text), FINGERPRINT_GROUP_CHARS)
    ]
    return "-".join(groups)
