"""Define the session records: a session, how a request arrived, and a session once authenticated.

A session is what a login at the Hive Entrance (the Hive's one HTTP door) opens (ADR-0033): a
random 256-bit bearer token the Entrance keeps only as its SHA-256, bound to a **binding key**
(the device's own Ed25519 key for a program, or a browser's non-extractable WebCrypto P-256 key
registered with its login challenge), tied to the listener it was opened on, with an absolute
expiry and an idle timeout, and possibly stepped up for a short window. ``Session`` is that row.
``Arrival`` is how a request reached the Entrance (which listener, from which address), and
``AuthenticatedSession`` is what a request that passed every check carries on: the session, its
device, the device's parsed ``CapabilitySet`` and whether it is stepped up right now.
``NonceClaim`` is one signed request's claim on its nonce. The Hive
Stand console's sessions are ``volatile``: kept in memory only, never written to the Entrance
tables (ADR-0033).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.session``. Built
    by the login flow and the session book; stored by ``hivemind.entrance.store.sessions``; read
    by every authenticated route. Calls into ``hivemind.entrance.auth`` primitives,
    ``hivemind.entrance.enrol.models``, ``hivemind.guard`` and waggle.

Key invariants:
    - A session holds the token's SHA-256, never the token.
    - A binding key is the raw 32-byte Ed25519 key or the 65-byte uncompressed P-256 point its
      kind says, as unpadded base64url.
    - ``ended_at`` and ``end_reason`` are set together or not at all; an ended session never opens
      again.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Sessions are bound
      to a key, and every request is signed".
    - hivemind.entrance.auth.session.book for the book that opens and ends sessions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from hivemind.entrance.auth.canonical import b64url_decode
from hivemind.entrance.auth.keys import ED25519_PUBLIC_KEY_BYTES, P256_PUBLIC_KEY_BYTES
from hivemind.entrance.auth.network import (
    MAX_ADDRESS_CHARS,
    address_network,
    is_device_network,
    trail_address,
)
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.guard import CapabilitySet
from waggle.messages.base import DeviceIdField, UtcDatetime

MAX_NETWORK_CHARS = 64  # An IPv6 /64 in CIDR text, or derp:<region>, with room to spare.
_SHA256_HEX = r"^[0-9a-f]{64}$"  # The token's SHA-256, lowercase hex: the session's key.
_P256_UNCOMPRESSED_PREFIX = 0x04  # SEC1's marker for the uncompressed point WebCrypto exports.

# A frozen, extras-forbidding config every model here shares (codingrules section 8.5).
_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")

__all__ = [
    "Arrival",
    "AuthenticatedSession",
    "BindingKind",
    "EndReason",
    "Listener",
    "NonceClaim",
    "Session",
]


class Listener(Enum):
    """Which of the Entrance's two listeners a request arrived on (ADR-0032)."""

    LOOPBACK = "loopback"  # The Hive Stand's own listener; always on; approval lives here.
    REMOTE = "remote"  # The exposed listener; exists only while exposed and not reduced.


class BindingKind(Enum):
    """Which kind of key a session is bound to: whose signature every request must carry."""

    ED25519 = "ed25519"  # A program or the CLI: the device's own enrolled Ed25519 key.
    P256 = "p256"  # A browser: its WebCrypto ECDSA P-256 session key, registered at login.


class EndReason(Enum):
    """Why a session ended; kept on its row as history."""

    LOGOUT = "logout"  # The device logged out.
    EXPIRED = "expired"  # session_ttl_hours (or the device's approval) ran out.
    IDLE = "idle"  # idle_timeout_minutes passed without a request.
    LOCKED = "locked"  # Its device was locked (lockout, denial burst, remote lock).
    REVOKED = "revoked"  # Its device was revoked.
    DEVICE_EXPIRED = "device_expired"  # Its device's approval lapsed.
    NOT_APPROVED = "not_approved"  # Its device was found not approved when sessions were checked.
    REDUCED = "reduced"  # The Entrance Reducer ended every remote session.


def _binding_key_text(value: str) -> str:
    """Refuse text that is not canonical base64url of a raw Ed25519 or uncompressed P-256 key."""
    size = len(b64url_decode(value))
    if size not in (ED25519_PUBLIC_KEY_BYTES, P256_PUBLIC_KEY_BYTES):
        raise ValueError("A binding key is a raw Ed25519 key or an uncompressed P-256 point.")
    return value


def _network_text(value: str) -> str:
    """Refuse anything but a canonical device network (hivemind.entrance.auth.network)."""
    if not is_device_network(value):
        raise ValueError("A session's network is a canonical /24, /64 or derp:<region>.")
    return value


BindingKeyText = Annotated[str, AfterValidator(_binding_key_text)]
NetworkText = Annotated[str, Field(max_length=MAX_NETWORK_CHARS), AfterValidator(_network_text)]


@dataclass(frozen=True, slots=True)
class Arrival:
    """How a request reached the Entrance: which listener, and from which network address.

    Attributes:
        listener: The listener it arrived on.
        address: The peer address the listener reported, as given (it keys the per-address rate
            limit); ``trail_address`` is what the trail may carry.
    """

    listener: Listener
    address: str

    @property
    def trail_address(self) -> str:
        """The address as the Pheromone Trail records it (never free text).

        Returns:
            The address, or a fixed placeholder when it does not look like one.
        """
        return trail_address(self.address)

    @property
    def network(self) -> str | None:
        """The address's own network: its /24 or /64; None when it is not an IP address.

        Returns:
            Canonical CIDR text, or None.
        """
        return address_network(self.address)


@dataclass(frozen=True, slots=True)
class NonceClaim:
    """One signed request's claim on its nonce, which a session table grants once.

    Attributes:
        nonce: The ``X-Hive-Nonce`` (or first frame's nonce), as sent.
        token_hash: The session the request was made on, for the record.
        expires_at: When the nonce may be forgotten: twice the skew window after the request.
        now: The request's moment; every claim that expired by then is purged first.
    """

    nonce: str
    token_hash: str
    expires_at: datetime
    now: datetime


class Session(BaseModel):
    """One login's session: the row the Entrance keeps for a token it never stores.

    Crosses into the Entrance tables (``entrance_sessions``) for every device but the console,
    whose sessions are volatile; never crosses the Landing Board, which hands the token out once.
    """

    model_config = _MODEL_CONFIG

    token_hash: str = Field(
        pattern=_SHA256_HEX, description="SHA-256 of the bearer token, lowercase hex."
    )
    device_id: DeviceIdField = Field(description="The device that logged in.")
    binding_kind: BindingKind = Field(description="Which kind of key signs its requests.")
    binding_key: BindingKeyText = Field(
        description="The binding public key, unpadded base64url: raw Ed25519 or an uncompressed "
        "P-256 point."
    )
    listener: Listener = Field(
        description="The listener it was opened on; the only one it works on."
    )
    address: str = Field(
        min_length=1,
        max_length=MAX_ADDRESS_CHARS,
        description="The address it logged in from, as the trail records it.",
    )
    network: NetworkText | None = Field(
        default=None, description="The network it logged in from; None when unknown."
    )
    created_at: UtcDatetime = Field(description="When the login succeeded.")
    last_seen_at: UtcDatetime = Field(description="When it last authenticated a request.")
    expires_at: UtcDatetime = Field(description="Its absolute end, however active it is.")
    stepped_up_until: UtcDatetime | None = Field(
        default=None, description="When its step-up window closes; None if never stepped up."
    )
    needs_step_up: bool = Field(
        default=False,
        description="The travel lock flagged its network as new: it must step up before anything "
        "else.",
    )
    volatile: bool = Field(
        default=False,
        description="The Hive Stand console's session: kept in memory only, never persisted.",
    )
    ended_at: UtcDatetime | None = Field(default=None, description="When it ended; None if open.")
    end_reason: EndReason | None = Field(default=None, description="Why it ended; None if open.")

    @property
    def is_open(self) -> bool:
        """Whether it has not ended (it may still have expired or gone idle).

        Returns:
            True while ``ended_at`` is None.
        """
        return self.ended_at is None

    def stepped_up_at(self, now: datetime) -> bool:
        """Return whether the session is stepped up at ``now``.

        Args:
            now: The moment to judge.

        Returns:
            True inside the step-up window and with no step-up still owed to the travel lock.
        """
        if self.needs_step_up or self.stepped_up_until is None:
            return False
        return now < self.stepped_up_until

    @model_validator(mode="after")
    def _is_consistent(self) -> Session:
        """Require the binding key to match its kind, ordered times and an end with its reason."""
        size = len(b64url_decode(self.binding_key))
        if self.binding_kind is BindingKind.ED25519 and size != ED25519_PUBLIC_KEY_BYTES:
            raise ValueError("An Ed25519 binding key is 32 raw bytes.")
        if self.binding_kind is BindingKind.P256 and (
            size != P256_PUBLIC_KEY_BYTES
            or b64url_decode(self.binding_key)[0] != _P256_UNCOMPRESSED_PREFIX
        ):
            raise ValueError("A P-256 binding key is a 65-byte uncompressed point.")
        if not self.created_at <= self.last_seen_at or self.expires_at <= self.created_at:
            raise ValueError("A session is seen after it is created and expires after that.")
        if (self.ended_at is None) != (self.end_reason is None):
            raise ValueError("ended_at and end_reason are set together or not at all.")
        return self


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    """A request that passed every check, and who it is: what every authenticated route receives.

    Attributes:
        session: The session as it stood when the request was admitted.
        device: The session's device, APPROVED when the request was admitted.
        capabilities: The device's capabilities, parsed by ``hivemind.guard``.
        stepped_up: Whether the session was stepped up when the request was admitted.
    """

    session: Session
    device: EnrolledDevice
    capabilities: CapabilitySet
    stepped_up: bool

    @property
    def interactive(self) -> bool:
        """Whether a person types at this device, so it may step up.

        Returns:
            The device's own ``interactive`` flag.
        """
        return self.device.interactive

    @property
    def needs_step_up(self) -> bool:
        """Whether the travel lock still owes this session a step-up before anything else.

        Returns:
            The session's own flag.
        """
        return self.session.needs_step_up
