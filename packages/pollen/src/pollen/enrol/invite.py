"""Read a ``pollen1.`` invite string: the secret an operator hands to a new device.

An operator mints an invite on the Hive Stand (the machine the Queen, the Hive's central
orchestrator, runs on) and gives it to the Pollen Packet (the thin gateway program on a device the
Hive borrows) by a channel they trust. The string is ``pollen1.`` and the URL-safe base64 of a
compact JSON object with exactly eight keys: the single-use ``token``, the Queen's ``url``, her
``hive_id``, the Hive Stand's ``hive_node_id`` and ``hive_key``, the certificate fingerprint
``tls_sha256`` (null for a loopback ``ws://`` address), ``expires_at`` and a ``label`` (enrolment.md
section 3.3). Reading it is the first step of the ceremony, and it refuses anything it cannot be
sure of: another prefix, a missing or extra key, a ``wss://`` address with nothing to pin, a
``ws://`` address that is not loopback, a fingerprint on a plain link, or an invite that has
already expired by this device's clock. The invite string and the token are secrets until used, so
no error message here quotes either: they name the rule that failed and nothing else.

Fits into the Hive:
    Parallel to hivemind's layer table, inside the pollen package. Called by pollen.enrol.enrol
    and pollen.cli; calls into waggle's id and URI rules only.

Key invariants:
    - The invite is parsed, never evaluated: the payload is JSON and every field is validated.
    - The set of keys is closed: an extra key is a different format version, not a newer one.
    - An error never carries the invite string, the token, the address or the key.

See Also:
    - docs/waggle/enrolment.md section 3.3 (the string) and section 4 (what the device checks).
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pollen.errors import InviteError
from waggle.messages.base import HiveIdField, NodeIdField, UtcDatetime
from waggle.messages.swarm.enrolment import (
    MAX_INVITE_TOKEN_CHARS,
    MIN_INVITE_TOKEN_CHARS,
    PUBLIC_KEY_HEX_PATTERN,
)
from waggle.uris import check_waggle_uri

INVITE_PREFIX = "pollen1."  # The format's version: another prefix is another format.
MAX_INVITE_CHARS = 4_096  # A generous bound on a string a person pastes; never a document.
SHA256_HEX = r"^[0-9a-f]{64}$"  # A certificate fingerprint: lowercase hex of a SHA-256 digest.

__all__ = ["INVITE_PREFIX", "MAX_INVITE_CHARS", "Invite", "parse_invite"]


class Invite(BaseModel):
    """What an invite string carries, validated."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(
        min_length=MIN_INVITE_TOKEN_CHARS,
        max_length=MAX_INVITE_TOKEN_CHARS,
        repr=False,
        description="The single-use secret; sent once, in the enrolment request.",
    )
    url: str = Field(description="The swarm listener's address: wss://, or ws:// on loopback.")
    hive_id: HiveIdField = Field(description="The Queen's bee address, the request's recipient.")
    hive_node_id: NodeIdField = Field(description="The Hive Stand's node id, pinned.")
    hive_key: Annotated[str, Field(pattern=PUBLIC_KEY_HEX_PATTERN)] = Field(
        description="The Hive Stand's node public key, 64 lowercase hex characters, pinned."
    )
    tls_sha256: Annotated[str, Field(pattern=SHA256_HEX)] | None = Field(
        description="The fingerprint of the listener's certificate; null for ws://."
    )
    expires_at: UtcDatetime = Field(description="When the invite stops being accepted.")
    label: str | None = Field(description="The operator's name for the device.")

    @model_validator(mode="after")
    def _link_rules(self) -> Invite:
        """Hold the link rules: a valid address and a pin exactly for wss://."""
        check_waggle_uri(self.url)
        if self.url.startswith("wss://") != (self.tls_sha256 is not None):
            raise ValueError("A wss:// address needs a fingerprint and a ws:// one has none.")
        return self


def parse_invite(text: str, now: datetime) -> Invite:
    """Read and validate an invite string.

    Args:
        text: The string the operator gave the device; surrounding whitespace is ignored.
        now: This device's clock, to refuse an invite that has already expired.

    Returns:
        The validated invite.

    Raises:
        InviteError: The string is not a usable invite. The message names the rule, never the text.
    """
    text = text.strip()
    if not text.startswith(INVITE_PREFIX) or len(text) > MAX_INVITE_CHARS:
        raise InviteError("The invite is not a pollen1 invite string.")
    try:
        payload = json.loads(_decode(text[len(INVITE_PREFIX) :]))
    except (binascii.Error, ValueError, RecursionError) as exc:
        raise InviteError("The invite string is damaged: it does not decode.") from exc
    if not isinstance(payload, dict):
        raise InviteError("The invite string is damaged: its payload is not an object.")
    try:
        invite = Invite.model_validate(payload)
    except ValidationError as exc:
        # Field locations only: the values are secrets or addresses and never go into a message.
        where = ", ".join(
            ".".join(str(part) for part in e["loc"]) or "invite" for e in exc.errors()
        )
        raise InviteError(f"The invite is not valid (see: {where}).") from exc
    if invite.expires_at <= now:
        raise InviteError("The invite has expired; ask the operator for a new one.")
    return invite


def _decode(body: str) -> bytes:
    """Decode URL-safe base64, with or without the padding."""
    return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
