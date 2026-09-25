"""Hold the Entrance's authentication: credential primitives, login, sessions, step-up and limits.

Login at the Hive Entrance (the Hive's one HTTP door) is two factors, the device's key and the
operator's password (ADR-0041), and every request is signed by a key bound to the session. The
modules directly here are the primitives those checks are built from, each pure or thin, calling
nothing else in the Entrance: ``canonical`` (every signed string and its encodings), ``keys`` (key
kinds, the Ed25519 and P-256 verifiers, the key fingerprint), ``network`` (a device's network and a
trail-safe address), ``password`` (Argon2id behind a two-slot semaphore), ``wrap`` (a private key
sealed under the password), ``passkeys`` (WebAuthn through the ``webauthn`` library),
``challenges`` (``ChallengeBook``: single-use, short-lived challenges bound to a subject) and
``fake`` (``SoftPasskey``). The sub-packages are the flows built on them and on the
enrolled-device model (roadmap 10.5e): ``session`` (the session records, tokens, ``SessionBook``
and signed-request authentication), ``login`` (the key proof first, then the password),
``step_up`` (when a step-up is required, the step-up itself, break-glass phrases), ``confirm``
(pending confirmations for devices that cannot step up), ``limits`` (rate limits and the
denial-burst lock) and ``travel`` (the travel lock).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. The primitives are
    used by ``hivemind.entrance.enrol`` and clients (``canonical``); the flows by the Landing
    Board's routes (later steps), the Entrance Reducer and, as the offboarder's sessions half,
    enrolment. The primitives call into ``hivemind.entrance.errors``, waggle, ``cryptography``
    and ``webauthn`` only; the flows also into ``hivemind.entrance.enrol``, the Entrance tables
    (through their protocols, never importing the store at runtime), ``hivemind.guard`` and
    ``httpx`` (tailscaled's local API).

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing here stores, logs or raises a password, a private key, a session token or a
      signature; tables hold hashes and public keys.
    - ``ChallengeBook``, the rate limiter and the denial windows are in memory by design (each
      says why); sessions, spent nonces, login failures and pending confirmations are persisted.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md for the decisions.
    - .claude/roadmap.md steps 10.4 and 10.5e for what this package serves.

Public API:
    - b64url_encode, b64url_decode, sha256_hex, new_nonce, path_and_query: the wire encodings.
    - enrol_string, login_string, request_string, websocket_string, webhook_string: every signed
      string, plus their tags (ENROL_TAG, LOGIN_TAG, REQUEST_TAG, WEBSOCKET_TAG, WEBHOOK_TAG) and
      MIN_NONCE_BYTES, NONCE_BYTES.
    - KeyKind, verify_ed25519, verify_p256, is_p256_point, key_fingerprint: device keys.
    - address_network, relay_network, is_device_network, trail_address: networks and addresses.
    - PasswordHasher, check_password_strength, MIN_PASSWORD_CHARS, MAX_PASSWORD_CHARS: the
      operator password.
    - wrap_private_key, unwrap_private_key: a private key sealed under the password.
    - RelyingParty, PasskeyRegistration, StoredPasskey, registration_options,
      authentication_options, registration_challenge, verify_registration,
      verify_authentication: WebAuthn.
    - ChallengeBook, Challenge, MAX_OPEN_CHALLENGES: single-use ceremony challenges.
    - SoftPasskey: a software WebAuthn authenticator for tests and demos.
    - Session, Arrival, AuthenticatedSession, Listener, BindingKind, EndReason, SessionBook,
      SessionRules, SessionGrant, OpenedSession, SignedRequest, SocketOpening,
      authenticate_request, authenticate_websocket, SOCKET_HELLO_DEADLINE_S: sessions (the rest
      via hivemind.entrance.auth.session).
    - AuthDeps, LoginCeremony, LoginGuards, DeviceProof, LoginChallenge, begin_login,
      finish_login, LOGIN_CHALLENGE_TTL: login.
    - ActionKind, StepUpReason, GoalSpend, requires_step_up, step_up, step_up_challenge,
      check_break_glass, BREAK_GLASS_PHRASES: step-up.
    - PendingConfirmation, PendingStatus, HeldAction, hold, confirm, cancel, expire_pending:
      pending confirmations.
    - RateLimiter, DenialCounter: limits.
    - TravelLock, open_travel_lock, PeerEndpointSource, TailscaleEndpointSource,
      FakePeerEndpointSource, tailscale_source: the travel lock.
"""

from hivemind.entrance.auth.canonical import (
    ENROL_TAG,
    LOGIN_TAG,
    MIN_NONCE_BYTES,
    NONCE_BYTES,
    REQUEST_TAG,
    WEBHOOK_TAG,
    WEBSOCKET_TAG,
    b64url_decode,
    b64url_encode,
    enrol_string,
    login_string,
    new_nonce,
    path_and_query,
    request_string,
    sha256_hex,
    webhook_string,
    websocket_string,
)
from hivemind.entrance.auth.challenges import MAX_OPEN_CHALLENGES, Challenge, ChallengeBook
from hivemind.entrance.auth.confirm import (
    HeldAction,
    PendingConfirmation,
    PendingStatus,
    cancel,
    confirm,
    expire_pending,
    hold,
)
from hivemind.entrance.auth.fake import SoftPasskey
from hivemind.entrance.auth.keys import (
    KeyKind,
    is_p256_point,
    key_fingerprint,
    verify_ed25519,
    verify_p256,
)
from hivemind.entrance.auth.limits import DenialCounter, RateLimiter
from hivemind.entrance.auth.login import (
    LOGIN_CHALLENGE_TTL,
    AuthDeps,
    DeviceProof,
    LoginCeremony,
    LoginChallenge,
    LoginGuards,
    begin_login,
    finish_login,
)
from hivemind.entrance.auth.network import (
    address_network,
    is_device_network,
    relay_network,
    trail_address,
)
from hivemind.entrance.auth.passkeys import (
    PasskeyRegistration,
    RelyingParty,
    StoredPasskey,
    authentication_options,
    registration_challenge,
    registration_options,
    verify_authentication,
    verify_registration,
)
from hivemind.entrance.auth.password import (
    MAX_PASSWORD_CHARS,
    MIN_PASSWORD_CHARS,
    PasswordHasher,
    check_password_strength,
)
from hivemind.entrance.auth.session import (
    SOCKET_HELLO_DEADLINE_S,
    Arrival,
    AuthenticatedSession,
    BindingKind,
    EndReason,
    Listener,
    OpenedSession,
    Session,
    SessionBook,
    SessionGrant,
    SessionRules,
    SignedRequest,
    SocketOpening,
    authenticate_request,
    authenticate_websocket,
)
from hivemind.entrance.auth.step_up import (
    BREAK_GLASS_PHRASES,
    ActionKind,
    GoalSpend,
    StepUpReason,
    check_break_glass,
    requires_step_up,
    step_up,
    step_up_challenge,
)
from hivemind.entrance.auth.travel import (
    FakePeerEndpointSource,
    PeerEndpointSource,
    TailscaleEndpointSource,
    TravelLock,
    open_travel_lock,
    tailscale_source,
)
from hivemind.entrance.auth.wrap import unwrap_private_key, wrap_private_key

__all__ = [
    "BREAK_GLASS_PHRASES",
    "ENROL_TAG",
    "LOGIN_CHALLENGE_TTL",
    "LOGIN_TAG",
    "MAX_OPEN_CHALLENGES",
    "MAX_PASSWORD_CHARS",
    "MIN_NONCE_BYTES",
    "MIN_PASSWORD_CHARS",
    "NONCE_BYTES",
    "REQUEST_TAG",
    "SOCKET_HELLO_DEADLINE_S",
    "WEBHOOK_TAG",
    "WEBSOCKET_TAG",
    "ActionKind",
    "Arrival",
    "AuthDeps",
    "AuthenticatedSession",
    "BindingKind",
    "Challenge",
    "ChallengeBook",
    "DenialCounter",
    "DeviceProof",
    "EndReason",
    "FakePeerEndpointSource",
    "GoalSpend",
    "HeldAction",
    "KeyKind",
    "Listener",
    "LoginCeremony",
    "LoginChallenge",
    "LoginGuards",
    "OpenedSession",
    "PasskeyRegistration",
    "PasswordHasher",
    "PeerEndpointSource",
    "PendingConfirmation",
    "PendingStatus",
    "RateLimiter",
    "RelyingParty",
    "Session",
    "SessionBook",
    "SessionGrant",
    "SessionRules",
    "SignedRequest",
    "SocketOpening",
    "SoftPasskey",
    "StepUpReason",
    "StoredPasskey",
    "TailscaleEndpointSource",
    "TravelLock",
    "address_network",
    "authenticate_request",
    "authenticate_websocket",
    "authentication_options",
    "b64url_decode",
    "b64url_encode",
    "begin_login",
    "cancel",
    "check_break_glass",
    "check_password_strength",
    "confirm",
    "enrol_string",
    "expire_pending",
    "finish_login",
    "hold",
    "is_device_network",
    "is_p256_point",
    "key_fingerprint",
    "login_string",
    "new_nonce",
    "open_travel_lock",
    "path_and_query",
    "registration_challenge",
    "registration_options",
    "relay_network",
    "request_string",
    "requires_step_up",
    "sha256_hex",
    "step_up",
    "step_up_challenge",
    "tailscale_source",
    "trail_address",
    "unwrap_private_key",
    "verify_authentication",
    "verify_ed25519",
    "verify_p256",
    "verify_registration",
    "webhook_string",
    "websocket_string",
    "wrap_private_key",
]
