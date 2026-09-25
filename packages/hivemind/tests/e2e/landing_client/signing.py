"""Build every signed string, header and first frame from the document's ``x-hive-signing``.

Every authenticated call to the Landing Board (the Hive Entrance's versioned API) is signed by the
session's binding key, and enrolment and login are signed by the device key (ADR-0041). A program
written from ``docs/entrance/openapi.json`` alone learns how from its ``x-hive-signing`` extension:
the tag and field list of each signed string, the header names, and the encodings (lowercase hex
SHA-256 digests, unpadded base64url nonces and signatures, integer Unix seconds). ``SigningRules``
reads exactly that. Each field the document lists is filled by its description, through a table
this client was written against, so a field the document renames or adds makes signing refuse
(changing a signed string is a breaking change, ADR-0042). Before anything is signed, the rules
rebuild the document's own worked example and refuse to start if the result differs from the
string the document prints, which proves the join and the encodings were read right.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by
    ``e2e.landing_client.client``; calls into ``cryptography`` and the standard library only.

Key invariants:
    - No value is signed unless the rules reproduced the document's worked example exactly.
    - No field may hold a line break, so no field can forge another line of a signed string.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from e2e.landing_client.document import as_object
from e2e.landing_client.schema import SchemaError

# The field descriptions of x-hive-signing's strings, mapped to the value this client fills each
# with. Written against the committed document: a description missing here refuses to sign.
FIELD_SLOTS: Mapping[str, str] = {
    "hive id": "hive_id",
    "invite code SHA-256 (hex)": "code_sha256",
    "Ed25519 public key (hex)": "public_key_hex",
    "device id": "device_id",
    "challenge nonce": "challenge",
    "method, upper case": "method",
    "raw path and query exactly as sent (path?query, or path alone)": "target",
    "raw path and query exactly as requested": "target",
    "X-Hive-Timestamp": "timestamp",
    "timestamp": "timestamp",
    "X-Hive-Nonce": "nonce",
    "nonce": "nonce",
    "request body SHA-256 (hex; of the empty string when there is no body)": "body_sha256",
    "subscription id": "subscription_id",
    "event id": "event_id",
    "body SHA-256 (hex)": "body_sha256",
}
_NONCE_FLOOR = re.compile(r"at least (\d+) random bytes")  # Read from x-hive-signing's nonce rule.
# Where the bearer token goes in the documented Authorization value; a placeholder, not a secret.
_TOKEN_PLACEHOLDER = "<token>"  # noqa: S105 -- x-hive-signing's placeholder text, not a credential
_LINE_BREAKS = ("\n", "\r")  # What would let one field forge another line.
# The dedupe header x-hive-signing's "webhooks" sentence names beside the two signing headers.
EVENT_ID_HEADER = "X-Hive-Event-Id"

__all__ = [
    "EVENT_ID_HEADER",
    "FIELD_SLOTS",
    "DeviceKey",
    "Session",
    "SigningRules",
    "b64url",
    "b64url_decode",
    "sha256_hex",
]


def b64url(data: bytes) -> str:
    """Encode bytes as unpadded base64url, the document's form for nonces and signatures.

    Args:
        data: Raw bytes.

    Returns:
        The text, without ``=`` padding.
    """
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode unpadded base64url (a signature a webhook carries).

    Args:
        text: The text, without padding.

    Returns:
        The bytes it encodes.
    """
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sha256_hex(data: bytes) -> str:
    """Return the lowercase hex SHA-256 the document's digests use.

    Args:
        data: The bytes to digest (a body, an invite code).

    Returns:
        64 lowercase hex characters.
    """
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class DeviceKey:
    """A program's Ed25519 device key, minted and kept by the program itself."""

    private: Ed25519PrivateKey = field(repr=False)

    @classmethod
    def generate(cls) -> DeviceKey:
        """Mint a fresh key.

        Returns:
            The new key.
        """
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_key_hex(self) -> str:
        """The raw 32-byte public key in lowercase hex, as enrolment sends it."""
        return self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()

    def sign(self, message: bytes) -> str:
        """Sign ``message`` and encode the raw 64-byte signature as the document says.

        Args:
            message: A signed string's bytes.

        Returns:
            The signature, unpadded base64url.
        """
        return b64url(self.private.sign(message))


@dataclass(frozen=True, slots=True)
class Session:
    """An open session: its bearer token and the key it is bound to (the device key)."""

    token: str = field(repr=False)
    device_id: str
    key: DeviceKey


class SigningRules:
    """The document's signing rules, checked against its own worked example."""

    def __init__(self, signing: Mapping[str, object]) -> None:
        """Read ``x-hive-signing`` and prove it was read right on the worked example.

        Args:
            signing: The document's ``x-hive-signing`` extension.

        Raises:
            SchemaError: A part is missing, or the example does not reproduce.
        """
        # Each signed string's field list, and the four header names, exactly as published.
        strings = as_object(signing.get("strings"), "strings")
        self._fields = {tag: _field_list(fields, tag) for tag, fields in strings.items()}
        self._headers = {
            role: str(name) for role, name in as_object(signing.get("headers"), "headers").items()
        }
        # The nonce size is stated in prose ("at least 16 random bytes"); read the number.
        floor = _NONCE_FLOOR.search(str(signing.get("nonce")))
        if floor is None:
            raise SchemaError("x-hive-signing does not say how many random bytes a nonce needs")
        self._nonce_bytes = int(floor.group(1))
        # The dedupe header is named only in prose; a renamed one must fail here, not silently.
        if EVENT_ID_HEADER not in str(signing.get("webhooks")):
            raise SchemaError(f"x-hive-signing's webhooks no longer name {EVENT_ID_HEADER}")
        self._check_example(as_object(signing.get("example"), "example"))

    def message(self, tag: str, values: Mapping[str, str]) -> bytes:
        """Build one signed string: its tag, then each documented field, one per line.

        Args:
            tag: The string's tag, e.g. ``hive-request-v1``.
            values: A value for each slot the tag's fields map to (``FIELD_SLOTS``).

        Returns:
            The UTF-8 bytes to sign.

        Raises:
            SchemaError: The document has no such string, names a field this client cannot fill,
                or a value holds a line break.
        """
        if tag not in self._fields:
            raise SchemaError(f"x-hive-signing describes no {tag} string")
        lines = [tag]
        for description in self._fields[tag]:
            slot = FIELD_SLOTS.get(description)
            if slot is None or slot not in values:
                raise SchemaError(f"{tag}: cannot fill the field {description!r}")
            if any(mark in values[slot] for mark in _LINE_BREAKS):
                raise SchemaError(f"{tag}: the field {description!r} holds a line break")
            lines.append(values[slot])
        # x-hive-signing's encoding: fields joined by one line feed, the whole encoded as UTF-8.
        return "\n".join(lines).encode("utf-8")

    def nonce(self) -> str:
        """Mint a fresh single-use nonce of the documented size.

        Returns:
            The nonce, unpadded base64url.
        """
        return b64url(secrets.token_bytes(self._nonce_bytes))

    def request_headers(
        self, session: Session, method: str, target: str, body: bytes, stamp: int | None = None
    ) -> dict[str, str]:
        """Return the four headers a signed request carries, by the documented names.

        Args:
            session: The session whose token and key sign the request.
            method: The HTTP method.
            target: The path and query exactly as sent.
            body: The body bytes exactly as sent (empty for none).
            stamp: The timestamp to sign; now when None (a test may pass a stale one).

        Returns:
            The Authorization, timestamp, nonce and signature headers.
        """
        timestamp = str(stamp if stamp is not None else int(time.time()))
        nonce = self.nonce()
        values = {
            "method": method.upper(),
            "target": target,
            "timestamp": timestamp,
            "nonce": nonce,
            "body_sha256": sha256_hex(body),
        }
        signature = session.key.sign(self.message("hive-request-v1", values))
        # The document spells the Authorization header as "Name: value template".
        name, _, template = self._headers["authorization"].partition(": ")
        return {
            name: template.replace(_TOKEN_PLACEHOLDER, session.token),
            self._headers["timestamp"]: timestamp,
            self._headers["nonce"]: nonce,
            self._headers["signature"]: signature,
        }

    def socket_hello(self, tag: str, session: Session, target: str) -> dict[str, object]:
        """Return a WebSocket's signed first frame for ``target``.

        Args:
            tag: The string the stream's first frame signs (its ``signs``).
            session: The session the socket belongs to.
            target: The socket's path and query exactly as requested.

        Returns:
            The frame's members: token, timestamp, nonce, signature.
        """
        stamp, nonce = int(time.time()), self.nonce()
        values = {"target": target, "timestamp": str(stamp), "nonce": nonce}
        signature = session.key.sign(self.message(tag, values))
        return {"token": session.token, "timestamp": stamp, "nonce": nonce, "signature": signature}

    def webhook_holds(
        self, hive_key_hex: str, subscription_id: str, headers: Mapping[str, str], body: bytes
    ) -> bool:
        """Return whether a webhook delivery is signed by the Hive, for this subscription.

        Args:
            hive_key_hex: The Hive's public key, pinned at enrolment (64 lowercase hex).
            subscription_id: The subscription the delivery claims to be for.
            headers: The delivery's headers.
            body: The delivery's exact body bytes.

        Returns:
            True when the signature over ``hive-webhook-v1`` verifies.
        """
        values = {
            "subscription_id": subscription_id,
            "event_id": headers[EVENT_ID_HEADER],
            "timestamp": headers[self._headers["timestamp"]],
            "body_sha256": sha256_hex(body),
        }
        public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(hive_key_hex))
        signature = b64url_decode(headers[self._headers["signature"]])
        try:
            public.verify(signature, self.message("hive-webhook-v1", values))
        except InvalidSignature:
            return False
        return True

    def _check_example(self, example: Mapping[str, object]) -> None:
        """Rebuild the document's worked example; refuse to sign if it does not reproduce."""
        expected = str(example.get("signed_string"))
        tag = expected.split("\n", 1)[0]
        values = {
            "method": str(example.get("method")).upper(),
            "target": str(example.get("target")),
            "timestamp": str(example.get("timestamp")),
            "nonce": str(example.get("nonce")),
            "body_sha256": sha256_hex(str(example.get("body")).encode("utf-8")),
        }
        if self.message(tag, values) != expected.encode("utf-8"):
            raise SchemaError("the rules do not reproduce x-hive-signing's worked example")


def _field_list(fields: object, tag: str) -> tuple[str, ...]:
    """Read one string's field descriptions, which must be a list of text."""
    if not isinstance(fields, list):
        raise SchemaError(f"x-hive-signing's {tag} is not a list of fields")
    return tuple(str(description) for description in fields)
