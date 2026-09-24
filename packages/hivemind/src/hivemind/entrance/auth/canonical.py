"""Define every string a device or the Hive signs at the Entrance, and the encodings they use.

A signature only works when signer and verifier build byte-for-byte the same message, and the Hive
Entrance (the Hive's one HTTP door) has five signers (a program enrolling, a device logging in,
every authenticated request, a WebSocket's first frame, the Hive signing a webhook) and clients
written in other languages from the published contract alone (ADR-0033). So every signed string, and
every encoding inside one, is defined here and nowhere else. Each string is a version tag, then its
fields, one per line (joined with a single line feed, U+000A) and encoded as UTF-8. Timestamps are
integer Unix seconds in decimal; digests are lowercase hex SHA-256; nonces are base64url without
padding over at least 16 random bytes; every signature on the wire is base64url without padding
(Ed25519: the raw 64 bytes; P-256: WebCrypto's IEEE P1363 ``r||s``, 64 bytes, which
``hivemind.entrance.auth.keys`` converts to DER before ``cryptography`` checks it). Each builder
validates its fields, so a field that could shift the line structure (an embedded newline) or be
written two ways (upper-case hex, padded base64) is refused rather than signed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Called by the
    Entrance's enrolment, login, request and WebSocket verification, by the webhook signer, and by
    clients (the CLI, the client guide's helper); its constants are published in the OpenAPI
    document's ``x-hive-signing`` extension. Calls into the standard library and waggle's id
    parser only.

Key invariants:
    - A builder's output is fully determined by its arguments: same fields, same bytes, on every
      platform.
    - No builder accepts a field containing a newline or a carriage return, so no field can forge
      another line of the message.
    - ``b64url_decode(b64url_encode(data)) == data`` for all bytes, and ``b64url_decode`` accepts
      only the one canonical spelling of each value (no padding, no stray bits).

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for where each string
      is signed and checked.
    - hivemind.entrance.auth.keys for the verifiers that consume these messages.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets

from waggle.errors import InvalidIdError
from waggle.ids import IdKind, parse_id

ENROL_TAG = "hive-enrol-v1"  # A program proves it holds the key it enrols (ADR-0033).
LOGIN_TAG = "hive-login-v1"  # A device proves it holds its key over a login challenge.
REQUEST_TAG = "hive-request-v1"  # A session's binding key signs every authenticated request.
WEBSOCKET_TAG = "hive-ws-v1"  # A WebSocket's first frame, since a browser cannot set headers.
WEBHOOK_TAG = "hive-webhook-v1"  # The Hive signs every webhook it delivers with its own key.
MIN_NONCE_BYTES = 16  # ADR-0033's floor: 128 random bits make a nonce collision a non-event.
NONCE_BYTES = 32  # new_nonce's default: a login challenge's 32 random bytes (ADR-0033).
SHA256_HEX_CHARS = 64  # A SHA-256 digest in lowercase hex.

_BASE64URL = re.compile(r"[A-Za-z0-9_-]*")  # The unpadded base64url alphabet, nothing else.
_LOWER_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")  # A SHA-256 digest or an Ed25519 key, in hex.
_HTTP_METHOD = re.compile(r"[A-Z]{1,16}")  # An HTTP method token after upper-casing.
_LINE_BREAKS = ("\n", "\r")  # Characters that would let a field forge a line of the message.

__all__ = [
    "ENROL_TAG",
    "LOGIN_TAG",
    "MIN_NONCE_BYTES",
    "NONCE_BYTES",
    "REQUEST_TAG",
    "SHA256_HEX_CHARS",
    "WEBHOOK_TAG",
    "WEBSOCKET_TAG",
    "b64url_decode",
    "b64url_encode",
    "enrol_string",
    "login_string",
    "new_nonce",
    "path_and_query",
    "request_string",
    "sha256_hex",
    "webhook_string",
    "websocket_string",
]


# ──────────────────────────────────────────────────────────────────────────────
# Encodings
# ──────────────────────────────────────────────────────────────────────────────


def b64url_encode(data: bytes) -> str:
    """Encode bytes as base64url without padding (RFC 4648 section 5), the Entrance's one form.

    Args:
        data: Any bytes: a nonce, a signature, a public key, a credential id.

    Returns:
        The unpadded base64url text.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode unpadded base64url, accepting only the canonical spelling of the value.

    Args:
        text: Unpadded base64url, as ``b64url_encode`` produces.

    Returns:
        The decoded bytes.

    Raises:
        ValueError: ``text`` uses a character outside the unpadded base64url alphabet, has an
            impossible length, or is not the canonical encoding of what it decodes to (stray bits
            in its last character), so no value has two spellings a replay cache could confuse.
    """
    # The alphabet check comes first: the standard decoder silently drops unknown characters.
    if _BASE64URL.fullmatch(text) is None or len(text) % 4 == 1:
        raise ValueError("The value is not unpadded base64url text.")
    try:
        data = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except binascii.Error as exc:
        raise ValueError("The value is not unpadded base64url text.") from exc
    if b64url_encode(data) != text:
        raise ValueError("The value is not the canonical base64url spelling of its bytes.")
    return data


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 of ``data``, the digest form every signed string uses.

    Args:
        data: The bytes to digest: a request body (empty for none), an invite code.

    Returns:
        64 lowercase hex characters.
    """
    return hashlib.sha256(data).hexdigest()


def new_nonce(size: int = NONCE_BYTES) -> str:
    """Mint a fresh nonce: ``size`` bytes from the CSPRNG, as unpadded base64url.

    Args:
        size: How many random bytes; at least ``MIN_NONCE_BYTES``.

    Returns:
        The nonce text, ready for a header or a challenge.

    Raises:
        ValueError: ``size`` is below ``MIN_NONCE_BYTES``.
    """
    if size < MIN_NONCE_BYTES:
        raise ValueError(f"A nonce needs at least {MIN_NONCE_BYTES} random bytes, not {size}.")
    return b64url_encode(secrets.token_bytes(size))


def path_and_query(raw_path: str, raw_query: str) -> str:
    """Join a request's raw path and raw query exactly as ``request_string`` signs them.

    Args:
        raw_path: The path exactly as sent, percent-encoding untouched.
        raw_query: The query string exactly as sent, without its ``?``; empty for none.

    Returns:
        ``raw_path``, plus ``?`` and the query when the query is non-empty.
    """
    return f"{raw_path}?{raw_query}" if raw_query else raw_path


# ──────────────────────────────────────────────────────────────────────────────
# Signed strings
# ──────────────────────────────────────────────────────────────────────────────


def enrol_string(hive_id: str, code_sha256_hex: str, public_key_hex: str) -> bytes:
    """Build what a program signs to prove it holds the Ed25519 key it enrols with.

    Args:
        hive_id: The Hive being joined (``hive_`` id), so a signature cannot be replayed at
            another Hive.
        code_sha256_hex: The invite code's SHA-256, lowercase hex.
        public_key_hex: The Ed25519 public key being enrolled, 64 lowercase hex characters.

    Returns:
        The UTF-8 message: tag, Hive id, code hash, key, one per line.

    Raises:
        ValueError: A field is malformed (see the module docstring).
    """
    return _message(
        ENROL_TAG,
        _hive_id(hive_id),
        _digest(code_sha256_hex, "invite code hash"),
        _digest(public_key_hex, "Ed25519 public key"),
    )


def login_string(hive_id: str, device_id: str, nonce: str) -> bytes:
    """Build what a device signs over its login challenge.

    Args:
        hive_id: The Hive being logged into.
        device_id: The device logging in (``device_`` id).
        nonce: The challenge the Entrance issued, as sent.

    Returns:
        The UTF-8 message: tag, Hive id, device id, challenge, one per line.

    Raises:
        ValueError: A field is malformed.
    """
    return _message(LOGIN_TAG, _hive_id(hive_id), _device_id(device_id), _nonce(nonce))


def request_string(
    method: str, raw_path_and_query: str, timestamp: int, nonce: str, body_sha256_hex: str
) -> bytes:
    """Build what a session's binding key signs for one authenticated request.

    Args:
        method: The HTTP method; signed upper-cased.
        raw_path_and_query: ``path_and_query(raw_path, raw_query)`` exactly as sent.
        timestamp: The ``X-Hive-Timestamp`` value, integer Unix seconds.
        nonce: The ``X-Hive-Nonce`` value.
        body_sha256_hex: The request body's SHA-256 (``sha256_hex(b"")`` for no body).

    Returns:
        The UTF-8 message: tag, method, target, timestamp, nonce, body digest, one per line.

    Raises:
        ValueError: A field is malformed.
    """
    return _message(
        REQUEST_TAG,
        _method(method),
        _field(raw_path_and_query, "request target"),
        _timestamp(timestamp),
        _nonce(nonce),
        _digest(body_sha256_hex, "body digest"),
    )


def websocket_string(raw_path_and_query: str, timestamp: int, nonce: str) -> bytes:
    """Build what a session's binding key signs in a WebSocket's first frame.

    Args:
        raw_path_and_query: The socket's path and query exactly as requested.
        timestamp: Integer Unix seconds.
        nonce: A fresh nonce.

    Returns:
        The UTF-8 message: tag, target, timestamp, nonce, one per line.

    Raises:
        ValueError: A field is malformed.
    """
    return _message(
        WEBSOCKET_TAG,
        _field(raw_path_and_query, "socket target"),
        _timestamp(timestamp),
        _nonce(nonce),
    )


def webhook_string(
    subscription_id: str, event_id: str, timestamp: int, body_sha256_hex: str
) -> bytes:
    """Build what the Hive signs, with its own key, for one webhook delivery.

    Args:
        subscription_id: The push subscription being delivered to.
        event_id: The event being delivered; also the receiver's idempotency key.
        timestamp: Integer Unix seconds at signing.
        body_sha256_hex: The delivered body's SHA-256.

    Returns:
        The UTF-8 message: tag, subscription, event, timestamp, body digest, one per line.

    Raises:
        ValueError: A field is malformed.
    """
    return _message(
        WEBHOOK_TAG,
        _field(subscription_id, "subscription id"),
        _field(event_id, "event id"),
        _timestamp(timestamp),
        _digest(body_sha256_hex, "body digest"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Field checks
# ──────────────────────────────────────────────────────────────────────────────


def _message(*lines: str) -> bytes:
    """Join already-checked lines with a line feed and encode them as UTF-8."""
    return "\n".join(lines).encode("utf-8")


def _field(value: str, label: str) -> str:
    """Refuse an empty field, or one whose line breaks could forge another line."""
    if not value or any(mark in value for mark in _LINE_BREAKS):
        raise ValueError(f"The {label} must be non-empty and on one line.")
    return value


def _hive_id(value: str) -> str:
    """Refuse anything but a well-formed Hive id."""
    return _waggle_id(value, IdKind.HIVE, "Hive id")


def _device_id(value: str) -> str:
    """Refuse anything but a well-formed device id."""
    return _waggle_id(value, IdKind.DEVICE, "device id")


def _waggle_id(value: str, kind: IdKind, label: str) -> str:
    """Parse ``value`` as a waggle id of ``kind``, reporting a malformed one as a ValueError."""
    try:
        return parse_id(value, kind)
    except InvalidIdError as exc:
        raise ValueError(f"The {label} is not a well-formed {kind.value}_ id.") from exc


def _digest(value: str, label: str) -> str:
    """Refuse anything but 64 lowercase hex characters (one spelling per value)."""
    if _LOWER_HEX_DIGEST.fullmatch(value) is None:
        raise ValueError(f"The {label} must be {SHA256_HEX_CHARS} lowercase hex characters.")
    return value


def _nonce(value: str) -> str:
    """Refuse a nonce that is not canonical base64url over at least MIN_NONCE_BYTES bytes."""
    if len(b64url_decode(value)) < MIN_NONCE_BYTES:
        raise ValueError(f"A nonce must carry at least {MIN_NONCE_BYTES} random bytes.")
    return value


def _timestamp(value: int) -> str:
    """Render integer Unix seconds in decimal, refusing a bool or a negative value."""
    # bool is an int subclass; True would otherwise sign as "1", a timestamp nobody meant.
    if isinstance(value, bool) or value < 0:
        raise ValueError("A timestamp must be a non-negative integer number of Unix seconds.")
    return str(value)


def _method(value: str) -> str:
    """Upper-case an HTTP method and refuse anything that is not a plain method token."""
    method = value.upper()
    if _HTTP_METHOD.fullmatch(method) is None:
        raise ValueError("An HTTP method must be letters only, for example GET or POST.")
    return method
