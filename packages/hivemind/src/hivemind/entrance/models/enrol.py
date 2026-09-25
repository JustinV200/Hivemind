"""Define the enrol resource's bodies: the Hive's id, a passkey's options, redeeming an invite.

A device joins by redeeming the single-use invite the operator minted on loopback (ADR-0041): a
program sends its Ed25519 public key with a signature over ``enrol_string`` (``hive-enrol-v1``, the
Hive id, the code's SHA-256, the key in hex), and may send a certificate signing request for its
mutual-TLS client certificate; a browser first asks for WebAuthn creation options for the code, then
sends the registration. Either way it describes itself (display text only) and learns its key's
fingerprint and the Hive's public key, which a program pins to verify webhooks. A program first
reads the Hive's id (``HiveView``), because its enrolment and login signatures name it and the
invite code does not carry it. These routes are unauthenticated; every string is bounded and the
code never comes back. ``RegistrationBody`` is the operator's offline registration of a device that
can reach no enrolment listener (loopback only).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.enrol``; published in the OpenAPI document. Calls into the
    enrolment model's ``DeviceDescription`` and pydantic.

Key invariants:
    - No body echoes the invite code or carries a private key.

See Also:
    - hivemind.entrance.enrol.redeem for the flow these bodies drive.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.entrance.enrol.certificates import MAX_CERTIFICATE_REQUEST_CHARS
from hivemind.entrance.enrol.models import MAX_DEVICE_NAME_CHARS, DeviceDescription, DisplayText
from waggle.messages.base import DeviceIdField, HiveIdField

MAX_CODE_CHARS = 64  # An invite code is 26 base32 characters; room for dashes and spaces.
MAX_KEY_CHARS = 256  # A 64-character hex key or a base64url signature, with room to spare.

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = [
    "Ed25519Redemption",
    "HiveView",
    "PasskeyOptionsRequest",
    "PasskeyOptionsView",
    "PasskeyRedemption",
    "RedemptionView",
    "RegistrationBody",
]


class HiveView(BaseModel):
    """The Hive a device is joining or logging into: what its signed strings name."""

    model_config = _CONFIG

    hive_id: HiveIdField = Field(
        description="The Hive's id (hive_...): hive-enrol-v1 and hive-login-v1 sign it, so a "
        "signature made for one Hive is refused by every other."
    )


class PasskeyOptionsRequest(BaseModel):
    """Ask for WebAuthn creation options for an invite (a browser, before redeeming)."""

    model_config = _CONFIG

    code: str = Field(
        max_length=MAX_CODE_CHARS, description="The invite code, as typed or scanned."
    )


class PasskeyOptionsView(BaseModel):
    """The options a browser creates its passkey with."""

    model_config = _CONFIG

    options: dict[str, JsonValue] = Field(
        description="PublicKeyCredentialCreationOptionsJSON for navigator.credentials.create "
        "(user verification required, no attestation), its challenge bound to the invite."
    )


class Ed25519Redemption(BaseModel):
    """Redeem an invite with a program's Ed25519 key."""

    model_config = _CONFIG

    code: str = Field(max_length=MAX_CODE_CHARS, description="The invite code.")
    public_key_hex: str = Field(
        max_length=MAX_KEY_CHARS, description="The raw 32-byte public key, 64 lowercase hex."
    )
    signature: str = Field(
        max_length=MAX_KEY_CHARS,
        description="The key's signature over enrol_string (hive-enrol-v1, the Hive id, the "
        "code's SHA-256 in hex, the key in hex), unpadded base64url.",
    )
    description: DeviceDescription = Field(description="What the device says about itself.")
    certificate_request: str | None = Field(
        default=None,
        max_length=MAX_CERTIFICATE_REQUEST_CHARS,
        description="A PEM PKCS#10 certificate signing request (Ed25519, P-256, P-384 or RSA of "
        "at least 2048 bits; its subject is ignored): when the Hive runs its own authority, the "
        "device's mutual-TLS client certificate is signed from it at approval and fetched from "
        "GET /v1/devices/me/certificate. Omit it for no certificate.",
    )


class RegistrationBody(BaseModel):
    """Register a device offline, from what the operator copied off it (loopback only)."""

    model_config = _CONFIG

    name: DisplayText = Field(
        min_length=1, max_length=MAX_DEVICE_NAME_CHARS, description="What the device is called."
    )
    public_key_hex: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Its raw 32-byte Ed25519 public key, 64 lowercase hex: what it logs in with.",
    )
    certificate_request: str = Field(
        min_length=1,
        max_length=MAX_CERTIFICATE_REQUEST_CHARS,
        description="Its PEM certificate signing request, for its mutual-TLS client certificate.",
    )


class PasskeyRedemption(BaseModel):
    """Redeem an invite with a browser's new passkey."""

    model_config = _CONFIG

    code: str = Field(max_length=MAX_CODE_CHARS, description="The invite code.")
    registration: dict[str, JsonValue] = Field(
        description="The PublicKeyCredential.toJSON() registration answering the options."
    )
    description: DeviceDescription = Field(description="What the device says about itself.")


class RedemptionView(BaseModel):
    """A redeemed invite: the request now waits for the operator on the Hive Stand."""

    model_config = _CONFIG

    device_id: DeviceIdField = Field(description="The device's record, now PENDING.")
    fingerprint: str = Field(
        description="The key's fingerprint; the operator sees the same one at approval."
    )
    hive_public_key_hex: str = Field(
        description="The Hive's Ed25519 public key in hex: pin it to verify webhooks."
    )
