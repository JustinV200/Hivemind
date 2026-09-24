"""Hold the Entrance's credential primitives: signed strings, device keys, passwords, passkeys.

Login at the Hive Entrance (the Hive's one HTTP door) is two factors, the device's key and the
operator's password (ADR-0033), and every request is signed by a key bound to the session. This
package holds the pieces those checks are built from, each pure or thin: ``canonical`` (every signed
string and its encodings), ``keys`` (key kinds, the Ed25519 and P-256 verifiers, the key
fingerprint), ``password`` (Argon2id hashing behind a two-slot semaphore), ``wrap`` (a private key
sealed under the password), ``passkeys`` (the WebAuthn ceremonies through the ``webauthn`` library),
``challenges`` (``ChallengeBook``: single-use, short-lived challenges bound to a subject, which
passkey enrolment uses now and login reuses) and ``fake`` (``SoftPasskey``, a software
authenticator for tests and demos). Sessions and step-up are a later step (roadmap 10.5e) built on
these.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Used by
    ``hivemind.entrance.enrol`` (the console bootstrap, invite redemption) and, later, the
    enrolment and login routes; clients reuse ``canonical``. Calls into
    ``hivemind.entrance.errors``, waggle, ``cryptography`` and ``webauthn`` only, never back into
    the rest of the Entrance.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing here stores, logs or raises a password, a private key or a session secret.
    - ``ChallengeBook`` is in memory by design: a restart only forces a new ceremony.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the decisions.
    - .claude/roadmap.md steps 10.4 and 10.5e for what this package serves.

Public API:
    - b64url_encode, b64url_decode, sha256_hex, new_nonce, path_and_query: the wire encodings.
    - enrol_string, login_string, request_string, websocket_string, webhook_string: every signed
      string, plus their tags (ENROL_TAG, LOGIN_TAG, REQUEST_TAG, WEBSOCKET_TAG, WEBHOOK_TAG) and
      MIN_NONCE_BYTES, NONCE_BYTES.
    - KeyKind, verify_ed25519, verify_p256, key_fingerprint: device keys and their checks.
    - PasswordHasher, check_password_strength, MIN_PASSWORD_CHARS, MAX_PASSWORD_CHARS: the
      operator password.
    - wrap_private_key, unwrap_private_key: a private key sealed under the password.
    - RelyingParty, PasskeyRegistration, StoredPasskey, registration_options,
      authentication_options, registration_challenge, verify_registration,
      verify_authentication: WebAuthn.
    - ChallengeBook, Challenge, MAX_OPEN_CHALLENGES: single-use ceremony challenges.
    - SoftPasskey: a software WebAuthn authenticator for tests and demos.
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
from hivemind.entrance.auth.fake import SoftPasskey
from hivemind.entrance.auth.keys import KeyKind, key_fingerprint, verify_ed25519, verify_p256
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
from hivemind.entrance.auth.wrap import unwrap_private_key, wrap_private_key

__all__ = [
    "ENROL_TAG",
    "LOGIN_TAG",
    "MAX_OPEN_CHALLENGES",
    "MAX_PASSWORD_CHARS",
    "MIN_NONCE_BYTES",
    "MIN_PASSWORD_CHARS",
    "NONCE_BYTES",
    "REQUEST_TAG",
    "WEBHOOK_TAG",
    "WEBSOCKET_TAG",
    "Challenge",
    "ChallengeBook",
    "KeyKind",
    "PasskeyRegistration",
    "PasswordHasher",
    "RelyingParty",
    "SoftPasskey",
    "StoredPasskey",
    "authentication_options",
    "b64url_decode",
    "b64url_encode",
    "check_password_strength",
    "enrol_string",
    "key_fingerprint",
    "login_string",
    "new_nonce",
    "path_and_query",
    "registration_challenge",
    "registration_options",
    "request_string",
    "sha256_hex",
    "unwrap_private_key",
    "verify_authentication",
    "verify_ed25519",
    "verify_p256",
    "verify_registration",
    "webhook_string",
    "websocket_string",
    "wrap_private_key",
]
