"""Define the Entrance's records: an enrolled device, its description, its invite, the operator.

These are the rows of the Entrance tables (the Hive Entrance is the Hive's one HTTP door;
codingrules Appendix C: "operator credential, enrolled devices", password hash and public keys
only). ``EnrolledDevice`` is one client of the Hive Entrance: its status in the
``hivemind.entrance.enrol.state`` machine, the public key it proves itself with (raw Ed25519, or a
passkey's COSE key and credential), what the operator bound at approval (name, capabilities, daily
spend cap, expiry, interactivity), and when it was last seen and from which network.
``DeviceDescription`` is what an unauthenticated device says about itself when it redeems an invite,
so every string in it is display text the model refuses to carry control characters in.
``DeviceInvite`` is the single-use invite, stored only as its code's SHA-256. ``OperatorCredential``
is the one operator row: an Argon2id PHC string, never a password. Every model is frozen and forbids
unknown fields; the cross-field rules (a passkey has a credential and is interactive, an approved
device has a key and an approval time) are validators, so a record that breaks them can neither be
built nor read back.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Persisted by
    ``hivemind.entrance.store`` (as each row's JSON body), built by
    ``hivemind.entrance.enrol.console`` and, later, the enrolment routes. Calls into
    ``hivemind.entrance.auth`` (key kinds, fingerprints, base64url) and
    ``hivemind.entrance.enrol.state``.

Key invariants:
    - Binary values (public keys, credential ids) are unpadded base64url text (the Entrance's one
      wire encoding, ``hivemind.entrance.auth.canonical``); an Ed25519 key decodes to 32 bytes.
    - Capabilities are kept as a sorted, duplicate-free tuple of strings: the grammar belongs to
      ``hivemind.guard``, which parses them before they are ever stored here.
    - No record holds a secret: the invite code, the password and every private key live nowhere
      in these models (the operator's hash is also left out of ``repr``).

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for every field.
    - hivemind.entrance.enrol.state for DeviceStatus and the transition table.
"""

from __future__ import annotations

import unicodedata
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from hivemind.entrance.auth.canonical import b64url_decode
from hivemind.entrance.auth.keys import ED25519_PUBLIC_KEY_BYTES, KeyKind, key_fingerprint
from hivemind.entrance.auth.network import is_device_network
from hivemind.entrance.enrol.state import DeviceStatus
from waggle.messages.base import DeviceIdField, UtcDatetime

MAX_DEVICE_NAME_CHARS = 64  # A label an operator reads in a list, not a paragraph.
MAX_PLATFORM_CHARS = 64  # "Android 16", "Windows 11", "python 3.12": short by nature.
MAX_USER_AGENT_CHARS = 512  # A browser User-Agent string, which can run long.
MAX_CAPABILITIES = 64  # A device holds a handful; the console holds about a dozen.
MAX_CAPABILITY_CHARS = 512  # A capability can carry a path or a host in its scope.
MAX_PUBLIC_KEY_CHARS = 2048  # base64url of a COSE key; an RSA-4096 passkey needs about 720.
MAX_CREDENTIAL_ID_CHARS = 1364  # WebAuthn's 1023-byte credential id cap, in base64url.
MAX_RP_ID_CHARS = 253  # A DNS name, the longest a relying party id can be.
MAX_PASSWORD_HASH_CHARS = 256  # The PHC string at this profile is about 100 characters.
_SHA256_HEX = r"^[0-9a-f]{64}$"  # A lowercase hex SHA-256: an invite code's hash.
# The PHC string cryptography's Argon2id writes; anything else (a plaintext password stored by a
# bug, above all) is refused before it can reach the Entrance tables.
_ARGON2ID_PHC = r"^\$argon2id\$v=19\$m=[0-9]+,t=[0-9]+,p=[0-9]+\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$"
# Bidirectional overrides and isolates: they can make a device name read as another on screen.
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")
_CONTROL_CATEGORY = "Cc"  # Unicode's control characters: escapes, newlines, NUL.
# Statuses in which a device has presented a key: from redemption until it leaves for good.
_KEYED_STATUSES = frozenset({DeviceStatus.PENDING, DeviceStatus.APPROVED, DeviceStatus.LOCKED})
# Statuses in which a device holds an approval: the operator bound it and it may log in or unlock.
_APPROVED_STATUSES = frozenset({DeviceStatus.APPROVED, DeviceStatus.LOCKED})

# A frozen, extras-forbidding config every model here shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "MAX_CAPABILITIES",
    "MAX_CAPABILITY_CHARS",
    "MAX_DEVICE_NAME_CHARS",
    "MAX_PLATFORM_CHARS",
    "MAX_USER_AGENT_CHARS",
    "CapabilityText",
    "DeviceDescription",
    "DeviceInvite",
    "DisplayText",
    "EnrolledDevice",
    "OperatorCredential",
]


def _display_text(value: str) -> str:
    """Refuse text a terminal or a page could be tricked by: control characters, bidi overrides."""
    # Every character is checked: one escape anywhere is enough to repaint an operator's terminal.
    for character in value:
        if unicodedata.category(character) == _CONTROL_CATEGORY or character in _BIDI_CONTROLS:
            raise ValueError("Display text may not contain control or bidirectional characters.")
    return value


def _canonical_base64url(value: str) -> str:
    """Refuse text that is not the one canonical unpadded base64url spelling of its bytes."""
    b64url_decode(value)
    return value


def _capability_text(value: str) -> str:
    """Refuse a capability string that cannot be one: not led by a lowercase letter, or controls."""
    if not ("a" <= value[:1] <= "z"):
        raise ValueError(f"A capability starts with a lowercase family name, not {value[:16]!r}.")
    return _display_text(value)


DisplayText = Annotated[str, AfterValidator(_display_text)]
Base64UrlText = Annotated[str, AfterValidator(_canonical_base64url)]
CapabilityText = Annotated[
    str, Field(min_length=1, max_length=MAX_CAPABILITY_CHARS), AfterValidator(_capability_text)
]


class DeviceDescription(BaseModel):
    """What a device says about itself when it redeems an invite, shown to the operator at approval.

    Unauthenticated and unverified: it crosses the Landing Board from a device nobody trusts yet,
    so it is display text only, bounded and free of control characters.
    """

    model_config = _MODEL_CONFIG

    name: DisplayText = Field(
        min_length=1,
        max_length=MAX_DEVICE_NAME_CHARS,
        description="The name the device proposes, e.g. 'Pixel 9'; the operator may rename it.",
    )
    platform: DisplayText = Field(
        default="",
        max_length=MAX_PLATFORM_CHARS,
        description="Operating system or runtime as the device reports it, e.g. 'Android 16'.",
    )
    user_agent: DisplayText = Field(
        default="",
        max_length=MAX_USER_AGENT_CHARS,
        description="The browser's User-Agent, or a program's name and version, as reported.",
    )


class EnrolledDevice(BaseModel):
    """One client of the Hive Entrance: its key, its standing, and what the operator allowed it.

    Created INVITED when an invite is minted (or APPROVED, for the Hive Stand console alone) and
    moved only through ``hivemind.entrance.store``'s status change, which applies the state
    machine. Crosses into the Entrance tables as a row's JSON body, and out through the Landing
    Board's (the Entrance's versioned API) device views.
    """

    model_config = _MODEL_CONFIG

    id: DeviceIdField = Field(description="The device's id, minted with waggle.ids.new_device_id.")
    name: DisplayText = Field(
        min_length=1,
        max_length=MAX_DEVICE_NAME_CHARS,
        description="The operator's name for it: the invite's label, then what approval bound.",
    )
    status: DeviceStatus = Field(description="Where it is in the enrolled-device state machine.")
    key_kind: KeyKind | None = Field(
        default=None, description="Which key it logs in with; None until it redeems its invite."
    )
    public_key: Base64UrlText | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PUBLIC_KEY_CHARS,
        description="Its public key, unpadded base64url: raw 32 bytes for Ed25519, the COSE key "
        "for a passkey. None until it redeems its invite.",
    )
    credential_id: Base64UrlText | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_CREDENTIAL_ID_CHARS,
        description="A passkey's credential id, unpadded base64url; None for Ed25519 devices.",
    )
    sign_count: int = Field(
        default=0,
        ge=0,
        description="The highest passkey signature counter accepted so far; 0 for Ed25519.",
    )
    rp_id: str | None = Field(
        default=None,
        max_length=MAX_RP_ID_CHARS,
        description="The WebAuthn relying party a passkey is bound to ('localhost' or the "
        "Entrance's DNS name); None for Ed25519 devices.",
    )
    backup_eligible: bool = Field(
        default=False,
        description="The passkey's BE flag at registration: it may be synced to other devices.",
    )
    backup_state: bool = Field(
        default=False,
        description="The passkey's BS flag at registration: it was synced, so the passkey "
        "provider's account is part of the factor.",
    )
    interactive: bool = Field(
        default=False,
        description="A human types the password at this device: always true for passkeys and "
        "the console; false for program keys unless the operator approved it as interactive.",
    )
    capabilities: tuple[CapabilityText, ...] = Field(
        default=(),
        max_length=MAX_CAPABILITIES,
        description="What it may do, as capability strings (hivemind.guard's grammar), sorted "
        "and without duplicates.",
    )
    spend_cap_usd_per_day: float | None = Field(
        default=None,
        ge=0,
        description="The most it may spend per day, in USD, on its own two factors: a goal "
        "past it needs a human's step-up (or a pending confirmation a human steps up to "
        "confirm, ADR-0033); None is uncapped, which only the console is.",
    )
    expires_at: UtcDatetime | None = Field(
        default=None,
        description="When its current standing lapses: the invite's expiry while INVITED, the "
        "request's while PENDING, the approval's once approved (None: never).",
    )
    loopback_bound: bool = Field(
        default=False,
        description="The Hive Stand console: its sessions open only on the loopback listener.",
    )
    description: DeviceDescription | None = Field(
        default=None, description="What it said about itself at redemption; None before."
    )
    created_at: UtcDatetime = Field(description="When the record was created (invite minted).")
    approved_at: UtcDatetime | None = Field(
        default=None, description="When the operator approved it; None until then."
    )
    last_seen_at: UtcDatetime | None = Field(
        default=None, description="When it last authenticated; None if it never has."
    )
    last_network: str | None = Field(
        default=None,
        description="The network it was last seen from: the /24 of an IPv4 address or the /64 "
        "of an IPv6 one as CIDR text, or derp:<region> when tailscaled reported the peer as "
        "relayed (hivemind.entrance.auth.network).",
    )

    @property
    def fingerprint(self) -> str | None:
        """The key fingerprint every approval surface shows; None before redemption.

        Returns:
            ``hivemind.entrance.auth.keys.key_fingerprint`` of the stored public key.
        """
        if self.public_key is None:
            return None
        return key_fingerprint(b64url_decode(self.public_key))

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_sorted_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Store capabilities in one order, once each, so equal sets compare equal."""
        return tuple(sorted(set(value)))

    @field_validator("last_network")
    @classmethod
    def _last_network_is_a_device_network(cls, value: str | None) -> str | None:
        """Refuse anything but a canonical IPv4 /24, IPv6 /64 or ``derp:<region>``."""
        if value is not None and not is_device_network(value):
            raise ValueError(
                "last_network must be a canonical IPv4 /24, IPv6 /64 or derp:<region> network."
            )
        return value

    @model_validator(mode="after")
    def _key_material_is_consistent(self) -> EnrolledDevice:
        """Require the key fields to match the key kind: all or nothing, and the right shape."""
        if (self.key_kind is None) != (self.public_key is None):
            raise ValueError("key_kind and public_key are set together or not at all.")
        if self.backup_state and not self.backup_eligible:
            raise ValueError("backup_state (BS) is only possible with backup_eligible (BE).")
        if self.key_kind is KeyKind.PASSKEY:
            _check_passkey(self)
        else:
            _check_not_passkey(self)
        return self

    @model_validator(mode="after")
    def _status_has_what_it_needs(self) -> EnrolledDevice:
        """Require what each status implies: a key once redeemed, an approval once approved."""
        if self.status is DeviceStatus.INVITED and (self.key_kind or self.description):
            raise ValueError("An INVITED device has not presented a key or a description yet.")
        if self.status in _KEYED_STATUSES and self.key_kind is None:
            raise ValueError(f"A {self.status.name} device must carry its public key.")
        if self.status is DeviceStatus.PENDING and self.description is None:
            raise ValueError("A PENDING device must carry the description it redeemed with.")
        if self.status in _APPROVED_STATUSES and self.approved_at is None:
            raise ValueError(f"A {self.status.name} device must carry approved_at.")
        if self.approved_at is not None and self.approved_at < self.created_at:
            raise ValueError("approved_at cannot precede created_at.")
        if self.loopback_bound and not self.interactive:
            raise ValueError("The loopback-bound console is always interactive.")
        return self


class DeviceInvite(BaseModel):
    """A single-use invite for one INVITED device, stored only as its code's SHA-256.

    The code itself is shown once, on the Hive Stand, as grouped text and a QR code, and never
    stored; the device presents it to redeem. Crosses into the Entrance tables only.
    """

    model_config = _MODEL_CONFIG

    code_hash: str = Field(
        pattern=_SHA256_HEX,
        description="SHA-256 of the invite code, lowercase hex: the invite's key; the code "
        "itself is never stored.",
    )
    device_id: DeviceIdField = Field(description="The INVITED device record this invite admits.")
    label: DisplayText = Field(
        min_length=1,
        max_length=MAX_DEVICE_NAME_CHARS,
        description="What the operator called the device when minting, e.g. 'phone'.",
    )
    created_at: UtcDatetime = Field(description="When the invite was minted.")
    expires_at: UtcDatetime = Field(description="When it stops admitting (invite_ttl_minutes).")
    used_at: UtcDatetime | None = Field(
        default=None, description="When it was redeemed; None while unused. Single use."
    )

    @model_validator(mode="after")
    def _times_are_ordered(self) -> DeviceInvite:
        """Require created_at < expires_at, and a use inside that window."""
        if self.expires_at <= self.created_at:
            raise ValueError("An invite must expire after it is created.")
        if self.used_at is not None and not self.created_at <= self.used_at < self.expires_at:
            raise ValueError("An invite can only be used between its creation and its expiry.")
        return self


class OperatorCredential(BaseModel):
    """The one operator row: the password's Argon2id hash and when it was set and last changed."""

    model_config = _MODEL_CONFIG

    password_hash: str = Field(
        max_length=MAX_PASSWORD_HASH_CHARS,
        pattern=_ARGON2ID_PHC,
        repr=False,
        description="The operator password's Argon2id PHC string; never the password itself.",
    )
    created_at: UtcDatetime = Field(description="When the password was first set.")
    changed_at: UtcDatetime = Field(description="When it was last set; created_at at first.")

    @model_validator(mode="after")
    def _changed_after_created(self) -> OperatorCredential:
        """Require changed_at to be no earlier than created_at."""
        if self.changed_at < self.created_at:
            raise ValueError("changed_at cannot precede created_at.")
        return self


def _check_passkey(device: EnrolledDevice) -> None:
    """Require a passkey device to carry its credential and relying party and be interactive."""
    if device.credential_id is None or device.rp_id is None:
        raise ValueError("A passkey device carries its credential_id and rp_id.")
    # WHY: every passkey ceremony requires user verification, so a person is always there.
    if not device.interactive:
        raise ValueError("A passkey device is always interactive.")


def _check_not_passkey(device: EnrolledDevice) -> None:
    """Require an Ed25519 (or not yet keyed) device to carry no passkey-only fields."""
    passkey_only = (device.credential_id, device.rp_id)
    if any(field is not None for field in passkey_only) or device.sign_count:
        raise ValueError("credential_id, rp_id and sign_count belong to passkey devices only.")
    if device.backup_eligible:
        raise ValueError("Only a passkey can be backup-eligible.")
    # An Ed25519 key is 32 raw bytes; anything else cannot verify a single signature.
    if device.public_key is not None and len(b64url_decode(device.public_key)) != (
        ED25519_PUBLIC_KEY_BYTES
    ):
        raise ValueError(f"An Ed25519 public key is {ED25519_PUBLIC_KEY_BYTES} raw bytes.")
