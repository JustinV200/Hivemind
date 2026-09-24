"""Define the auth resource's request and response bodies: login, logout and step-up.

Login at the Landing Board (the Hive Entrance's versioned API) is two calls (ADR-0033): a challenge
for the device (a browser registers the WebCrypto P-256 key its session will be bound to in the same
call), then the device's proof over it plus the operator password, which opens a session and hands
its token back once. Step-up repeats the ceremony on an open session. Every model forbids unknown
fields and bounds every string, so a flood of oversized bodies costs nothing to refuse; the
password is a ``SecretStr``, never in a ``repr`` or a log.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.auth``; published in ``docs/entrance/openapi.json``. Calls into
    pydantic and waggle's id types only.

Key invariants:
    - The token appears only in ``OpenedSessionView``, answered once to the device that logged in.
    - No model carries a private key.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Login is the device
      key plus the password, the key proof first".
    - hivemind.entrance.auth.login for the flow these bodies drive.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr

from waggle.messages.base import DeviceIdField

MAX_PROOF_CHARS = 256  # A nonce, an Ed25519 signature or a P-256 key in base64url: far below.
MAX_PASSWORD_FIELD_CHARS = 1_024  # The password rule's own ceiling (MAX_PASSWORD_CHARS).

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = [
    "ChallengeRequest",
    "ChallengeView",
    "LoginRequest",
    "OpenedSessionView",
    "StepUpRequest",
    "SteppedUpView",
]


class ChallengeRequest(BaseModel):
    """Ask for a login challenge for one device (unauthenticated)."""

    model_config = _CONFIG

    device_id: DeviceIdField = Field(description="The device that will log in.")
    binding_key: str | None = Field(
        default=None,
        max_length=MAX_PROOF_CHARS,
        description="A browser's WebCrypto ECDSA P-256 session key, the uncompressed point as "
        "unpadded base64url; its session will be bound to it. Omit for an Ed25519 device.",
    )


class ChallengeView(BaseModel):
    """A login (or step-up) challenge: what the device answers."""

    model_config = _CONFIG

    device_id: DeviceIdField = Field(description="The device it was issued for.")
    nonce: str = Field(
        description="The challenge, unpadded base64url; an Ed25519 device signs login_string "
        "(hive-login-v1, the Hive id, its device id, this nonce) with its key."
    )
    expires_at: datetime = Field(description="When it can no longer be answered (60 s).")
    passkey_options: dict[str, JsonValue] | None = Field(
        default=None,
        description="For a passkey device: PublicKeyCredentialRequestOptionsJSON for "
        "navigator.credentials.get (user verification required). Null for an Ed25519 device.",
    )


class LoginRequest(BaseModel):
    """Answer a login challenge: the device's proof, then the operator password."""

    model_config = _CONFIG

    device_id: DeviceIdField = Field(description="The device logging in.")
    nonce: str = Field(max_length=MAX_PROOF_CHARS, description="The challenge being answered.")
    signature: str | None = Field(
        default=None,
        max_length=MAX_PROOF_CHARS,
        description="An Ed25519 device's signature over login_string, unpadded base64url.",
    )
    assertion: dict[str, JsonValue] | None = Field(
        default=None,
        description="A passkey device's PublicKeyCredential.toJSON() assertion over the challenge.",
    )
    binding_key: str | None = Field(
        default=None,
        max_length=MAX_PROOF_CHARS,
        description="A browser's P-256 session key, exactly as sent with the challenge.",
    )
    password: SecretStr = Field(
        max_length=MAX_PASSWORD_FIELD_CHARS,
        description="The operator password; checked only after the device proof holds.",
    )


class OpenedSessionView(BaseModel):
    """A new session: its bearer token, handed out this once, and how long it lives."""

    model_config = _CONFIG

    token: str = Field(
        description="The session's bearer token: send it as Authorization: Bearer on every "
        "request, signed by the binding key (x-hive-signing). The Entrance keeps only its hash."
    )
    device_id: DeviceIdField = Field(description="The device the session belongs to.")
    listener: str = Field(description="The listener it works on: loopback or remote.")
    expires_at: datetime = Field(description="Its absolute end (session_ttl_hours at most).")
    needs_step_up: bool = Field(
        description="The travel lock saw a new network: step up before anything else."
    )


class StepUpRequest(BaseModel):
    """Answer a step-up challenge: a fresh device proof, and the password on an Ed25519 device."""

    model_config = _CONFIG

    nonce: str = Field(max_length=MAX_PROOF_CHARS, description="The step-up challenge answered.")
    signature: str | None = Field(
        default=None,
        max_length=MAX_PROOF_CHARS,
        description="An Ed25519 device's signature over login_string for this challenge.",
    )
    assertion: dict[str, JsonValue] | None = Field(
        default=None, description="A passkey device's fresh assertion with user verification."
    )
    password: SecretStr | None = Field(
        default=None,
        max_length=MAX_PASSWORD_FIELD_CHARS,
        description="The operator password: required for an Ed25519 device, optional for a "
        "passkey (its user verification is the person).",
    )


class SteppedUpView(BaseModel):
    """A session just stepped up."""

    model_config = _CONFIG

    stepped_up_until: datetime = Field(
        description="When the step-up window closes (step_up_window_minutes)."
    )
