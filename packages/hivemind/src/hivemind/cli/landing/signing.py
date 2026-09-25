"""Sign what a CLI device sends the Landing Board, byte for byte as the Entrance verifies it.

The Landing Board (the Hive Entrance's versioned HTTP contract, ADR-0040) admits only enrolled
devices, and every one of them signs: its enrolment (``hive-enrol-v1``), its login challenge
(``hive-login-v1``), every authenticated request (``hive-request-v1``) and every WebSocket's first
frame (``hive-ws-v1``), ADR-0041. The strings themselves are built by
``hivemind.entrance.auth``'s canonical builders, never re-implemented here, so the CLI can never
sign a message the Entrance would build differently. This module only adds what a signer does
around them: the Ed25519 signature in the wire's one encoding (unpadded base64url of the raw 64
bytes, where ``waggle.signing`` answers padded standard base64), the four headers a signed request
carries, and a socket's first frame. Every function is pure over its arguments, the timestamp and
the nonce included, so a test can check the exact bytes against the Entrance's own verifier.

Fits into the Hive:
    Layer 7 (the terminal), inside ``hivemind.cli.landing``. Called by
    ``hivemind.cli.landing.client`` and ``.stream``. Calls into ``hivemind.entrance.auth`` (the
    canonical strings and encodings), ``hivemind.entrance.enrol`` (the invite code's hash) and
    ``waggle.signing``.

Key invariants:
    - Every signed message comes from a ``hivemind.entrance.auth`` builder.
    - No return value, ``repr`` or error here carries a private key or a session token; a
      signature is returned only to be sent.

See Also:
    - hivemind.entrance.auth.canonical for every signed string and encoding.
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for where each one is
      signed and checked.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

from hivemind.entrance.auth import (
    b64url_encode,
    enrol_string,
    login_string,
    new_nonce,
    request_string,
    sha256_hex,
    websocket_string,
)
from hivemind.entrance.enrol import invite_code_hash
from waggle.clock import Clock
from waggle.signing import Ed25519Signer

AUTHORIZATION = "Authorization"  # The bearer token's header.
TIMESTAMP = "X-Hive-Timestamp"  # Integer Unix seconds at signing.
NONCE = "X-Hive-Nonce"  # A fresh nonce per request; the Entrance keeps it to refuse a replay.
SIGNATURE = "X-Hive-Signature"  # The binding key's signature over hive-request-v1.

__all__ = [
    "AUTHORIZATION",
    "NONCE",
    "SIGNATURE",
    "TIMESTAMP",
    "Credential",
    "DeviceKey",
    "OutgoingRequest",
    "Stamp",
    "enrol_signature",
    "first_frame",
    "login_signature",
    "request_headers",
    "sign",
]


@dataclass(frozen=True, slots=True)
class DeviceKey:
    """A device's id and the Ed25519 key it signs with.

    Attributes:
        device_id: The enrolled device (``device_...``).
        signer: Its private key; never printed (the field is left out of the ``repr``).
    """

    device_id: str
    signer: Ed25519Signer = field(repr=False)


@dataclass(frozen=True, slots=True)
class Credential:
    """What every authenticated message is signed with: a session's token and its binding key.

    Attributes:
        token: The session's bearer token; held in memory only and never printed.
        key: The device key the session is bound to (a program's binding key is its device key).
    """

    token: str = field(repr=False)
    key: DeviceKey


@dataclass(frozen=True, slots=True)
class OutgoingRequest:
    """One request exactly as it will be sent.

    Attributes:
        method: The HTTP method.
        target: The path and query exactly as sent (``/v1/chat?limit=1``).
        body: The body bytes exactly as sent; empty for none.
    """

    method: str
    target: str
    body: bytes = field(default=b"", repr=False)


@dataclass(frozen=True, slots=True)
class Stamp:
    """When a message is signed, and the nonce that makes it single-use.

    Attributes:
        timestamp: Integer Unix seconds.
        nonce: Unpadded base64url over fresh random bytes.
    """

    timestamp: int
    nonce: str

    @classmethod
    def now(cls, clock: Clock) -> Stamp:
        """Stamp a message at ``clock``'s current second with a fresh nonce.

        Args:
            clock: The time source; the Entrance refuses a timestamp outside its skew window.

        Returns:
            The stamp.
        """
        return cls(timestamp=int(clock.now().timestamp()), nonce=new_nonce())


def sign(signer: Ed25519Signer, message: bytes) -> str:
    """Sign ``message`` and encode the signature the way the Landing Board reads it.

    Args:
        signer: The device's key.
        message: A canonical string from ``hivemind.entrance.auth``.

    Returns:
        Unpadded base64url of the raw 64-byte Ed25519 signature.
    """
    # waggle's signer answers padded standard base64 (its own wire form); the Entrance's is
    # base64url without padding, and it accepts only that one spelling.
    return b64url_encode(base64.b64decode(signer.sign(message)))


def enrol_signature(hive_id: str, code: str, signer: Ed25519Signer) -> str:
    """Prove, while redeeming an invite, that the device holds the key it enrols.

    Args:
        hive_id: The Hive being joined; the signature cannot be replayed at another Hive.
        code: The invite code, in any form a person typed it.
        signer: The device's new key.

    Returns:
        The signature over ``enrol_string``.

    Raises:
        ValueError: ``code`` is not an invite code, or ``hive_id`` is not a Hive id.
    """
    message = enrol_string(hive_id, invite_code_hash(code), signer.public_key_bytes.hex())
    return sign(signer, message)


def login_signature(hive_id: str, key: DeviceKey, nonce: str) -> str:
    """Answer a login (or step-up) challenge with the device key.

    Args:
        hive_id: The Hive being logged into.
        key: The device's id and key.
        nonce: The challenge exactly as the Entrance issued it.

    Returns:
        The signature over ``login_string``.

    Raises:
        ValueError: A field is malformed (``hivemind.entrance.auth.canonical``).
    """
    return sign(key.signer, login_string(hive_id, key.device_id, nonce))


def request_headers(
    credential: Credential, request: OutgoingRequest, stamp: Stamp
) -> dict[str, str]:
    """Return the four headers that make ``request`` an authenticated Landing Board request.

    Args:
        credential: The session's token and binding key.
        request: The method, target and body exactly as they will be sent.
        stamp: The timestamp and nonce to sign with.

    Returns:
        ``Authorization``, ``X-Hive-Timestamp``, ``X-Hive-Nonce`` and ``X-Hive-Signature``.
    """
    message = request_string(
        request.method, request.target, stamp.timestamp, stamp.nonce, sha256_hex(request.body)
    )
    return {
        AUTHORIZATION: f"Bearer {credential.token}",
        TIMESTAMP: str(stamp.timestamp),
        NONCE: stamp.nonce,
        SIGNATURE: sign(credential.key.signer, message),
    }


def first_frame(credential: Credential, target: str, stamp: Stamp) -> str:
    """Return a WebSocket's first frame: the token and a signature over the socket's opening.

    Args:
        credential: The session's token and binding key.
        target: The socket's path and query exactly as requested.
        stamp: The timestamp and nonce to sign with.

    Returns:
        The frame's JSON text (a ``SocketHello``).
    """
    message = websocket_string(target, stamp.timestamp, stamp.nonce)
    frame = {
        "token": credential.token,
        "timestamp": stamp.timestamp,
        "nonce": stamp.nonce,
        "signature": sign(credential.key.signer, message),
    }
    return json.dumps(frame)
