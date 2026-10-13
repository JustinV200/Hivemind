"""Mint a device invite's token and build or parse the `pollen1.` string that carries it.

Enrolment starts with the operator: `hive swarm invite` mints a single-use token and prints one
invite string, `pollen1.<base64url(JSON)>`, which `pollen enrol` on the device reads
(docs/waggle/enrolment.md section 3). The token is 16 random bytes from the system's CSPRNG as
RFC 4648 base32 in groups of four, the same shape as the Landing Board's invite code, and only its
SHA-256 is ever stored. The string wraps the token with everything the device needs to find and
verify the right Hive: the listener's advertised URL, the Queen's bee address (the recipient of
the device's first frame), the Hive Stand's node id and public key, the SHA-256 fingerprint of the
listener's certificate, the expiry and the operator's label. This module mints the token and
builds and parses that string exactly as section 3.3 specifies: eight keys, no padding written,
padding accepted on reading, any other prefix rejected. It mirrors `hivemind.entrance.enrol.
invite` on purpose and does not import it, because the Entrance sits above the Swarm in the layer
table; the two keep the same token shape so an operator learns one habit.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside `hivemind.swarm.enrolment`.
    Called by the enrolment door (`mint_invite`) and by `hive swarm invite`; the parse half is
    the reference the packet's own reader is held to and what tests decode strings with. Calls
    into hivemind.swarm.errors and waggle only.

Key invariants:
    - The token exists only in the string returned to the operator: never in a table, a trail
      event, a log line or an error message, and a parse error never repeats the text it read.
    - `build_invite_string` writes exactly the eight keys of section 3.3, in that order, as
      compact UTF-8 JSON, base64url without padding; `parse_invite_string` accepts the padded form
      too and rejects a missing, extra or malformed key.
    - A `wss://` invite carries the listener's certificate fingerprint and a `ws://` one carries
      none, and a `ws://` URL names a loopback host only: a Real Cell is never reached in
      plaintext off the machine.

See Also:
    - docs/waggle/enrolment.md section 3.3 for the format this module implements.
    - docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md for the trust model.
    - hivemind.entrance.enrol.invite for the Landing Board's invite code this one mirrors.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import secrets
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hivemind.swarm.errors import InviteFormatError
from waggle.messages.base import HiveIdField, NodeIdField, UtcDatetime
from waggle.messages.swarm.enrolment import PUBLIC_KEY_HEX_PATTERN
from waggle.uris import check_waggle_uri, is_loopback_host

INVITE_PREFIX = "pollen1."  # The format's version: any other prefix is a different format.
INVITE_TOKEN_BYTES = 16  # 128 random bits (ADR-0051): unguessable in an invite's minutes of life.
INVITE_TOKEN_GROUP_CHARS = 4  # "ABCD-EFGH-...": groups an operator compares and types at a glance.
# The shape of a token as it travels: 26 base32 characters in groups of four joined by hyphens.
INVITE_CODE_PATTERN = r"^[A-Z2-7]{4}(-[A-Z2-7]{4}){5}-[A-Z2-7]{2}$"
MAX_INVITE_STRING_CHARS = 4_096  # A real invite is ~400 characters; this only bounds hostile input.
_GROUP_SEPARATOR = "-"  # Between groups of the token as it travels.
_BASE32_PADDING = "="  # base32 pads 16 bytes to 32 characters; the six "=" carry nothing.
_BASE64_BLOCK = 4  # base64 text is a multiple of 4 characters; padding is added back to read.
_SHA256_HEX = r"^[0-9a-f]{64}$"  # A fingerprint: lowercase hex of 32 bytes.

__all__ = [
    "INVITE_CODE_PATTERN",
    "INVITE_PREFIX",
    "InviteContents",
    "InviteEndpoint",
    "build_invite_string",
    "new_invite_token",
    "parse_invite_string",
    "token_hash",
]


class InviteEndpoint(BaseModel):
    """Where a device finds the Hive and what it pins about it: the invite's address half.

    Built by the composition root from the listener's advertised URL and certificate and the Hive
    Stand's own identity, and handed to the enrolment door, which copies it into every invite.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(
        max_length=512,
        description="The swarm listener's advertised URL: wss://, or ws:// on a loopback host.",
    )
    hive_id: HiveIdField = Field(
        description="The Queen's bee address, the recipient of the device's first frame."
    )
    hive_node_id: NodeIdField = Field(description="The Hive Stand's node id.")
    hive_key_hex: str = Field(
        pattern=PUBLIC_KEY_HEX_PATTERN,
        description="The Hive key: the Hive Stand's node public key, 64 lowercase hex characters.",
    )
    tls_sha256: str | None = Field(
        default=None,
        pattern=_SHA256_HEX,
        description="The listener certificate's SHA-256 fingerprint (DER), or None for ws://.",
    )

    @model_validator(mode="after")
    def _url_and_pin_agree(self) -> InviteEndpoint:
        """Require a pin exactly for wss://, and a loopback host for ws://."""
        check_waggle_uri(self.url)
        parts = urlsplit(self.url)
        is_tls = parts.scheme == "wss"
        # A device pins the certificate instead of trusting an authority, so a wss:// invite with
        # no fingerprint would leave it nothing to verify the listener against.
        if is_tls != (self.tls_sha256 is not None):
            raise ValueError("An invite carries tls_sha256 exactly when its URL is wss://.")
        # The onion-service exception of waggle.uris belongs to Night Veil Cells; a Real Cell is
        # reached in plaintext on the machine itself or not at all.
        if not is_tls and not is_loopback_host(parts.hostname or ""):
            raise ValueError("A ws:// invite URL must name a loopback host.")
        return self


class InviteContents(BaseModel):
    """What a parsed invite string says: the token, the address half and the lifetime."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(
        pattern=INVITE_CODE_PATTERN, repr=False, description="The single-use secret token."
    )
    endpoint: InviteEndpoint = Field(description="Where the Hive is and what to pin about it.")
    expires_at: UtcDatetime = Field(description="When the invite stops being accepted.")
    label: str | None = Field(default=None, max_length=64, description="The operator's label.")


class _InviteWire(BaseModel):
    """The eight keys of section 3.3, in order, exactly as they travel inside the string."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(pattern=INVITE_CODE_PATTERN)
    url: str
    hive_id: HiveIdField
    hive_node_id: NodeIdField
    hive_key: str = Field(pattern=PUBLIC_KEY_HEX_PATTERN)
    tls_sha256: str | None = Field(pattern=_SHA256_HEX)
    expires_at: UtcDatetime
    label: str | None = Field(max_length=64)


def new_invite_token() -> str:
    """Mint a fresh invite token: 128 bits from the CSPRNG, grouped base32.

    Returns:
        26 base32 characters in groups of four joined by hyphens (32 characters in all).
    """
    raw = base64.b32encode(secrets.token_bytes(INVITE_TOKEN_BYTES)).decode("ascii")
    compact = raw.rstrip(_BASE32_PADDING)
    step = INVITE_TOKEN_GROUP_CHARS
    return _GROUP_SEPARATOR.join(compact[i : i + step] for i in range(0, len(compact), step))


def token_hash(token: str) -> str:
    """Return the SHA-256 an invite is stored under: of the token exactly as it travels.

    Args:
        token: The token as the invite carries it and the device presents it.

    Returns:
        64 lowercase hex characters; the only trace of the token the Hive keeps.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_invite_string(
    token: str, endpoint: InviteEndpoint, expires_at: UtcDatetime, label: str | None
) -> str:
    """Build the `pollen1.<base64url(JSON)>` string the operator hands to the device.

    Args:
        token: A token from `new_invite_token`.
        endpoint: The address half: URL, Hive ids and key, certificate fingerprint.
        expires_at: When the invite stops being accepted (timezone-aware).
        label: The operator's label for the device, or None.

    Returns:
        The invite string. A secret until used or expired: show it once, never log it.

    Raises:
        pydantic.ValidationError: The token is not in the token shape, or a field is malformed.
    """
    wire = _InviteWire(
        token=token,
        url=endpoint.url,
        hive_id=endpoint.hive_id,
        hive_node_id=endpoint.hive_node_id,
        hive_key=endpoint.hive_key_hex,
        tls_sha256=endpoint.tls_sha256,
        expires_at=expires_at,
        label=label,
    )
    # Compact, in field order (which is section 3.3's order), UTF-8; the receiver re-parses it, so
    # the byte layout carries no meaning beyond being valid JSON.
    document = json.dumps(wire.model_dump(mode="json"), separators=(",", ":"), ensure_ascii=False)
    encoded = base64.urlsafe_b64encode(document.encode("utf-8")).decode("ascii")
    return INVITE_PREFIX + encoded.rstrip("=")


def parse_invite_string(text: str) -> InviteContents:
    """Read an invite string the way a device does: prefix, base64url, JSON, eight keys.

    Args:
        text: The invite string, with or without base64 padding, surrounding whitespace ignored.

    Returns:
        The contents, validated.

    Raises:
        InviteFormatError: The prefix is not `pollen1.`, the base64 or JSON is malformed, a key
            is missing, extra or badly formed, or the URL, fingerprint and scheme disagree. The
            message never repeats the text.
    """
    stripped = "".join(text.split())
    if len(stripped) > MAX_INVITE_STRING_CHARS or not stripped.startswith(INVITE_PREFIX):
        raise InviteFormatError(f"An invite string starts with {INVITE_PREFIX} and is one token.")
    body = stripped[len(INVITE_PREFIX) :]
    padded = body + "=" * (-len(body) % _BASE64_BLOCK)
    # validate=True rejects characters outside the alphabet instead of silently dropping them.
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        wire = _InviteWire.model_validate(json.loads(decoded.decode("utf-8")))
        endpoint = InviteEndpoint(
            url=wire.url,
            hive_id=wire.hive_id,
            hive_node_id=wire.hive_node_id,
            hive_key_hex=wire.hive_key,
            tls_sha256=wire.tls_sha256,
        )
    except (binascii.Error, ValueError, ValidationError) as exc:
        # ValidationError is a ValueError, but naming it keeps the intent plain: any malformed
        # layer is the same refusal, and the cause is chained without its input.
        raise InviteFormatError(
            f"The text is not a valid {INVITE_PREFIX} invite string: {type(exc).__name__}."
        ) from None
    return InviteContents(
        token=wire.token, endpoint=endpoint, expires_at=wire.expires_at, label=wire.label
    )
