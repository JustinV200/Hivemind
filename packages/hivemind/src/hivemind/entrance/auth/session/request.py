"""Authenticate a signed request, or a WebSocket's first frame, against its session.

A stolen session token at the Hive Entrance (the Hive's one HTTP door) is useless alone (ADR-0041):
every authenticated request carries ``Authorization: Bearer <token>``, ``X-Hive-Timestamp``,
``X-Hive-Nonce`` and ``X-Hive-Signature``, the session's binding key's signature over
``request_string`` (the method, the raw path and query exactly as sent, the timestamp, the nonce and
the body's SHA-256), and a WebSocket's first frame carries the same four values signed over
``websocket_string`` (a browser cannot set headers on a socket). ``authenticate_request`` and
``authenticate_websocket`` refuse, with one generic ``AuthenticationFailedError``, a token that is
unknown, ended, expired or idle, whose device is no longer APPROVED, a signature that does not
verify under the binding key (Ed25519, or P-256 in WebCrypto's P1363 form), a request on another
listener than the session's, a timestamp more than ``request_skew_s`` from the Hive Stand's clock,
a nonce already spent (persisted, so a restart does not reopen a replay window) and, for a browser's
socket, an ``Origin`` other than the Entrance's own. A live token with a bad signature, or a good
signature over a spent nonce, is a Guard signal and is recorded as ``guard.entrance_login_failed``.
The five-second deadline for the first frame belongs to the socket route; the constant is here.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.session``. Called
    by the Landing Board's authentication dependency and its socket routes (later steps). Calls
    into the session book, ``hivemind.entrance.auth.canonical`` and ``.keys``, and
    ``hivemind.guard`` (the device's capabilities).

Key invariants:
    - The signature is checked over bytes built from what arrived, never from a re-serialised
      form of it.
    - A nonce is spent only by a request whose signature verified; a refused one never burns a
      legitimate client's nonce.
    - No header value, token, signature or body appears in an event, a log line or an error.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Sessions are bound
      to a key, and every request is signed".
    - hivemind.entrance.auth.canonical for the signed strings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hivemind.entrance.auth.canonical import (
    b64url_decode,
    path_and_query,
    request_string,
    sha256_hex,
    websocket_string,
)
from hivemind.entrance.auth.keys import verify_ed25519, verify_p256
from hivemind.entrance.auth.session.book import LiveSession, SessionBook
from hivemind.entrance.auth.session.failures import (
    Failure,
    FailureReason,
    FailureStep,
    record_failure,
)
from hivemind.entrance.auth.session.models import (
    Arrival,
    AuthenticatedSession,
    BindingKind,
    NonceClaim,
)
from hivemind.entrance.auth.session.token import bearer_credential, token_hash
from hivemind.entrance.errors import AuthenticationFailedError
from hivemind.guard import CapabilitySet

SOCKET_HELLO_DEADLINE_S = 5.0  # ADR-0041: a socket with no valid first frame by then is closed.
MAX_FIRST_FRAME_CHARS = 2_048  # Four short fields; anything longer is refused unread.
AUTHORIZATION_HEADER = "authorization"  # Header names, lower-cased as they are compared.
TIMESTAMP_HEADER = "x-hive-timestamp"
NONCE_HEADER = "x-hive-nonce"
SIGNATURE_HEADER = "x-hive-signature"
_SIGNED_HEADERS = (AUTHORIZATION_HEADER, TIMESTAMP_HEADER, NONCE_HEADER, SIGNATURE_HEADER)
_TIMESTAMP = re.compile(r"[0-9]{1,12}")  # Integer Unix seconds, ASCII digits only.
_MAX_FIELD_CHARS = 256  # A nonce or a signature in base64url; far below any real header limit.
# The verifier for each kind of binding key; both answer True or False and never raise.
_VERIFIERS: Mapping[BindingKind, Callable[[bytes, bytes, bytes], bool]] = {
    BindingKind.ED25519: verify_ed25519,
    BindingKind.P256: verify_p256,
}

__all__ = [
    "MAX_FIRST_FRAME_CHARS",
    "SOCKET_HELLO_DEADLINE_S",
    "SignedRequest",
    "SocketHello",
    "SocketOpening",
    "authenticate_request",
    "authenticate_websocket",
]


@dataclass(frozen=True, slots=True)
class SignedRequest:
    """One HTTP request exactly as it arrived, for ``authenticate_request``.

    Attributes:
        method: The HTTP method.
        raw_path: The path exactly as sent, percent-encoding untouched.
        raw_query: The query string exactly as sent, without its ``?``.
        body: The raw body bytes (empty for none).
        headers: The request's headers; names are compared case-insensitively, and a signing
            header that appears twice refuses the request.
        arrival: The listener and address it arrived on.
    """

    method: str
    raw_path: str
    raw_query: str
    body: bytes = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    arrival: Arrival


@dataclass(frozen=True, slots=True)
class SocketOpening:
    """A WebSocket's opening: its first frame and where it was requested, for the socket check.

    Attributes:
        first_frame: The first text frame, a ``SocketHello`` as JSON.
        raw_path: The socket's path exactly as requested.
        raw_query: Its query string exactly as requested, without its ``?``.
        origin: The handshake's ``Origin`` header, or None when it had none.
        arrival: The listener and address the socket arrived on.
    """

    first_frame: str = field(repr=False)
    raw_path: str
    raw_query: str
    origin: str | None
    arrival: Arrival


class SocketHello(BaseModel):
    """A WebSocket's first frame: the session's token and a signature over the socket's opening.

    Crosses the Landing Board from the client as JSON; bounded, so a flood of large frames costs
    nothing to refuse.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    token: str = Field(max_length=_MAX_FIELD_CHARS, description="The session's bearer token.")
    timestamp: int = Field(ge=0, description="Integer Unix seconds at signing.")
    nonce: str = Field(max_length=_MAX_FIELD_CHARS, description="A fresh nonce, base64url.")
    signature: str = Field(
        max_length=_MAX_FIELD_CHARS,
        description="The binding key's signature over websocket_string, unpadded base64url.",
    )


@dataclass(frozen=True, slots=True)
class _Presented:
    """What a client presented, parsed, and the exact bytes its signature must cover."""

    token: str = field(repr=False)
    timestamp: int
    nonce: str
    signature: bytes = field(repr=False)
    message: bytes = field(repr=False)
    arrival: Arrival
    step: FailureStep
    origin: str | None = None  # A socket's Origin; checked only for a browser's session.


async def authenticate_request(
    book: SessionBook, request: SignedRequest, now: datetime
) -> AuthenticatedSession:
    """Authenticate one signed HTTP request against its session.

    Args:
        book: The session book.
        request: The request exactly as it arrived.
        now: The Hive Stand's clock when it arrived.

    Returns:
        The session, its device and capabilities, and whether it is stepped up.

    Raises:
        AuthenticationFailedError: Any check failed; never says which.
    """
    presented = _presented_request(request)
    if presented is None:
        raise AuthenticationFailedError()
    admitted = await _admit(book, presented, now)
    return _authenticated(admitted, now)


async def authenticate_websocket(
    book: SessionBook, opening: SocketOpening, now: datetime
) -> AuthenticatedSession:
    """Authenticate a WebSocket's first frame against its session.

    Args:
        book: The session book.
        opening: The first frame, the socket's target, its ``Origin`` and where it arrived.
        now: The Hive Stand's clock when the frame arrived.

    Returns:
        The session, its device and capabilities, and whether it is stepped up.

    Raises:
        AuthenticationFailedError: Any check failed, including a browser socket from a foreign
            origin; never says which.
    """
    presented = _presented_socket(opening)
    if presented is None:
        raise AuthenticationFailedError()
    admitted = await _admit(book, presented, now)
    return _authenticated(admitted, now)


# ──────────────────────────────────────────────────────────────────────────────
# Reading what arrived
# ──────────────────────────────────────────────────────────────────────────────


def _presented_request(request: SignedRequest) -> _Presented | None:
    """Parse a request's four signing headers and build the bytes they sign; None if malformed."""
    headers = _signing_headers(request.headers)
    if headers is None:
        return None
    token = bearer_credential(headers.get(AUTHORIZATION_HEADER))
    stamp, nonce = headers.get(TIMESTAMP_HEADER), headers.get(NONCE_HEADER)
    signature = headers.get(SIGNATURE_HEADER)
    if token is None or stamp is None or nonce is None or signature is None:
        return None
    # Digits only (str.isdigit would also take superscripts), and nothing oversized.
    if _TIMESTAMP.fullmatch(stamp) is None or max(len(nonce), len(signature)) > _MAX_FIELD_CHARS:
        return None
    target = path_and_query(request.raw_path, request.raw_query)
    try:
        # request_string refuses a nonce that is not canonical base64url over >= 16 bytes.
        message = request_string(
            request.method, target, int(stamp), nonce, sha256_hex(request.body)
        )
        raw_signature = b64url_decode(signature)
    except ValueError:
        return None
    return _Presented(
        token, int(stamp), nonce, raw_signature, message, request.arrival, FailureStep.REQUEST
    )


def _presented_socket(opening: SocketOpening) -> _Presented | None:
    """Parse a socket's first frame and build the bytes it signs; None if malformed."""
    if len(opening.first_frame) > MAX_FIRST_FRAME_CHARS:
        return None
    target = path_and_query(opening.raw_path, opening.raw_query)
    try:
        hello = SocketHello.model_validate_json(opening.first_frame)
        message = websocket_string(target, hello.timestamp, hello.nonce)
        raw_signature = b64url_decode(hello.signature)
    except (ValidationError, ValueError):
        return None
    return _Presented(
        hello.token,
        hello.timestamp,
        hello.nonce,
        raw_signature,
        message,
        opening.arrival,
        FailureStep.SOCKET,
        origin=opening.origin,
    )


def _signing_headers(headers: Mapping[str, str]) -> dict[str, str] | None:
    """Return the four signing headers by lower-cased name; None if one appears twice."""
    found: dict[str, str] = {}
    # items() of a multi-valued header mapping (Starlette's) yields every copy of a header.
    for name, value in headers.items():
        lowered = name.lower()
        if lowered not in _SIGNED_HEADERS:
            continue
        # Two values for one signing header: which one was signed is ambiguous, so neither is.
        if lowered in found:
            return None
        found[lowered] = value
    return found


# ──────────────────────────────────────────────────────────────────────────────
# Judging it against the session
# ──────────────────────────────────────────────────────────────────────────────


async def _admit(book: SessionBook, presented: _Presented, now: datetime) -> LiveSession:
    """Run every check in order and spend the nonce last; return the live session."""
    try:
        hashed = token_hash(presented.token)
    except ValueError:
        raise AuthenticationFailedError() from None
    # Latency: one or two local reads (the session, its device).
    live = await book.live(hashed, now)
    if live is None:
        raise AuthenticationFailedError()
    session = live.session
    verify = _VERIFIERS[session.binding_kind]
    # A live token without its binding key's signature: a stolen token, and a Guard signal.
    if not verify(b64url_decode(session.binding_key), presented.message, presented.signature):
        raise await _signal(book, live, presented, FailureReason.REQUEST_SIGNATURE)
    if not _in_bounds(book, live, presented, now):
        raise AuthenticationFailedError()
    claim = NonceClaim(presented.nonce, hashed, now + 2 * book.rules.request_skew, now)
    # Latency: one local transaction; a spent nonce under a good signature is a replay.
    if not await book.tables.claim_nonce(claim):
        raise await _signal(book, live, presented, FailureReason.REQUEST_REPLAY)
    await book.tables.touch(hashed, now)
    return live


def _in_bounds(book: SessionBook, live: LiveSession, presented: _Presented, now: datetime) -> bool:
    """Check the listener, the clock skew and, for a browser's socket, the Origin."""
    session = live.session
    # A session works only on the listener it was opened on (ADR-0041).
    if session.listener is not presented.arrival.listener:
        return False
    if abs(now.timestamp() - presented.timestamp) > book.rules.request_skew.total_seconds():
        return False
    # Only a browser holds a P-256 binding key, and only a browser is fooled by a foreign page.
    if presented.step is FailureStep.SOCKET and session.binding_kind is BindingKind.P256:
        allowed = book.rules.origins.get(session.listener, frozenset())
        return presented.origin is not None and presented.origin in allowed
    return True


async def _signal(
    book: SessionBook, live: LiveSession, presented: _Presented, reason: FailureReason
) -> AuthenticationFailedError:
    """Record a Guard signal against the session's device and return the generic refusal."""
    failure = Failure(reason, presented.step, presented.arrival, live.session.device_id)
    return await record_failure(book.records, failure)


def _authenticated(live: LiveSession, now: datetime) -> AuthenticatedSession:
    """Package an admitted session for the route: its device's capabilities, its step-up."""
    return AuthenticatedSession(
        session=live.session,
        device=live.device,
        capabilities=CapabilitySet.parse(*live.device.capabilities),
        stepped_up=live.session.stepped_up_at(now),
    )
