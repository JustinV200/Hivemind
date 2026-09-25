"""Hold sessions: their records, tokens, the book that opens and ends them, and request checks.

A login at the Hive Entrance (the Hive's one HTTP door) opens a session: a 256-bit bearer token kept
only as its SHA-256, bound to a key, tied to its listener, with an absolute expiry and an idle
timeout (ADR-0041). ``models`` holds the records (``Session``, ``Arrival``,
``AuthenticatedSession``, ``NonceClaim`` and the enums); ``token`` mints, hashes and reads tokens;
``book`` is ``SessionBook``, which opens sessions, judges whether one is alive and ends them (it is
the sessions half of the enrolment step's ``DeviceOffboarder``, and what the Entrance Reducer ends
remote sessions through); ``request`` authenticates a signed request or a socket's first frame
against its session; ``failures`` records the Guard signal every refused authentication leaves.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth``. Used by login,
    step-up, the Reducer and the Landing Board's routes (later steps). Calls into the Entrance
    tables (``hivemind.entrance.store.sessions``, through ``SessionTable``), the enrolled-device
    model, the trail and ``hivemind.guard``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No token, signature, header value or password is stored, logged, raised or recorded.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Sessions are bound
      to a key, and every request is signed".

Public API:
    - Session, Arrival, AuthenticatedSession, NonceClaim, Listener, BindingKind, EndReason: the
      records (models).
    - mint_token, token_hash, bearer_credential, TOKEN_BYTES, TOKEN_CHARS: tokens (token).
    - SessionBook, SessionRules, SessionGrant, OpenedSession, LiveSession, DEVICE_END_REASONS,
      LOGIN_KIND, SESSION_ENDED_KIND: the book and the events it records (book).
    - authenticate_request, authenticate_websocket, SignedRequest, SocketOpening, SocketHello,
      SOCKET_HELLO_DEADLINE_S, MAX_FIRST_FRAME_CHARS: request and socket checks (request).
    - record_failure, Failure, FailureReason, FailureStep, LOGIN_FAILED_KIND: the Guard signal
      (failures).
"""

from hivemind.entrance.auth.session.book import (
    DEVICE_END_REASONS,
    LOGIN_KIND,
    SESSION_ENDED_KIND,
    LiveSession,
    OpenedSession,
    SessionBook,
    SessionGrant,
    SessionRules,
)
from hivemind.entrance.auth.session.failures import (
    LOGIN_FAILED_KIND,
    Failure,
    FailureReason,
    FailureStep,
    record_failure,
)
from hivemind.entrance.auth.session.models import (
    Arrival,
    AuthenticatedSession,
    BindingKind,
    EndReason,
    Listener,
    NonceClaim,
    Session,
)
from hivemind.entrance.auth.session.request import (
    MAX_FIRST_FRAME_CHARS,
    SOCKET_HELLO_DEADLINE_S,
    SignedRequest,
    SocketHello,
    SocketOpening,
    authenticate_request,
    authenticate_websocket,
)
from hivemind.entrance.auth.session.token import (
    TOKEN_BYTES,
    TOKEN_CHARS,
    bearer_credential,
    mint_token,
    token_hash,
)

__all__ = [
    "DEVICE_END_REASONS",
    "LOGIN_FAILED_KIND",
    "LOGIN_KIND",
    "MAX_FIRST_FRAME_CHARS",
    "SESSION_ENDED_KIND",
    "SOCKET_HELLO_DEADLINE_S",
    "TOKEN_BYTES",
    "TOKEN_CHARS",
    "Arrival",
    "AuthenticatedSession",
    "BindingKind",
    "EndReason",
    "Failure",
    "FailureReason",
    "FailureStep",
    "Listener",
    "LiveSession",
    "NonceClaim",
    "OpenedSession",
    "Session",
    "SessionBook",
    "SessionGrant",
    "SessionRules",
    "SignedRequest",
    "SocketHello",
    "SocketOpening",
    "authenticate_request",
    "authenticate_websocket",
    "bearer_credential",
    "mint_token",
    "record_failure",
    "token_hash",
]
