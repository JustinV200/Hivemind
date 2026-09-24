"""Wrap each listener's application: security headers, the loopback check, per-address limits.

Three checks run on every request before any route sees it, as plain ASGI wrappers around the
FastAPI application so no response, a server error included, escapes them (ADR-0033):
``SecurityHeaders`` adds ``Content-Security-Policy: default-src 'self'; object-src 'none';
frame-ancestors 'none'`` (and ``nosniff``, ``no-referrer``) to every response, so nothing can run
foreign script in the Entrance's origin or frame it; ``LoopbackGate`` (loopback listener only)
answers a bare 403 to any request whose ``Host`` is not a loopback name on this listener's port or
that carries a forwarding header, so no proxy or DNS-rebinding page can front the routes that
approve devices; ``AddressLimit`` holds every peer address to ``rate_limit_per_address``,
unauthenticated routes and WebSocket handshakes included, and answers 429 past it.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Wrapped around
    each application by ``hivemind.entrance.app``. Calls into
    ``hivemind.entrance.expose.loopback_request_allowed`` and the rate limiter.

Key invariants:
    - The loopback check and the address limit run before routing, on HTTP and WebSocket alike.
    - A refused WebSocket is closed before it is accepted (the server answers 403).
    - No header value is logged.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Exposure never means
      the open internet" and "Browsers get nothing that can run foreign script".
    - hivemind.entrance.expose.loopback for the Host and forwarding-header rule.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from hivemind.entrance.auth.limits import RateLimiter
from hivemind.entrance.expose import loopback_request_allowed

CONTENT_SECURITY_POLICY = "default-src 'self'; object-src 'none'; frame-ancestors 'none'"
_SECURITY_HEADERS = (
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode("ascii")),
    (b"x-content-type-options", b"nosniff"),  # A browser never guesses a type into script.
    (b"referrer-policy", b"no-referrer"),  # No Entrance URL leaks to anything it links to.
)
_GATED_SCOPES = frozenset({"http", "websocket"})  # Lifespan and anything else pass untouched.
_FORBIDDEN = 403  # The loopback check's bare refusal.
_TOO_MANY = 429  # The address limit's refusal.
_POLICY_VIOLATION = 1008  # RFC 6455: a WebSocket refused for policy (sent before accepting).

__all__ = [
    "CONTENT_SECURITY_POLICY",
    "AddressLimit",
    "LoopbackGate",
    "SecurityHeaders",
]


class SecurityHeaders:
    """Add the Entrance's security headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        """Wrap ``app``.

        Args:
            app: The application (or the next wrapper) to protect.
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward the request, adding the headers to the response's start."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            """Append the security headers to the response start; pass everything else on."""
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", ()), *_SECURITY_HEADERS]
            await send(message)

        await self._app(scope, receive, send_with_headers)


class LoopbackGate:
    """Refuse a loopback request with a foreign ``Host`` or any forwarding header: a bare 403."""

    def __init__(self, app: ASGIApp, bound_port: int) -> None:
        """Wrap ``app``.

        Args:
            app: The loopback application.
            bound_port: The port the loopback listener actually bound (an OS-chosen 0 resolved).
        """
        self._app = app
        self._port = bound_port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward an allowed request; refuse any other before it reaches a route."""
        if scope["type"] not in _GATED_SCOPES:
            await self._app(scope, receive, send)
            return
        names, host = _header_names_and_host(scope)
        if loopback_request_allowed(host, names, self._port):
            await self._app(scope, receive, send)
            return
        await _refuse(scope, send, _FORBIDDEN)


class AddressLimit:
    """Hold every peer address to its token bucket, unauthenticated requests included."""

    def __init__(self, app: ASGIApp, limiter: RateLimiter) -> None:
        """Wrap ``app``.

        Args:
            app: The application (or the next wrapper).
            limiter: The Entrance's rate limiter; its per-address buckets are charged here.
        """
        self._app = app
        self._limiter = limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward a request within its address's rate; answer 429 past it."""
        if scope["type"] not in _GATED_SCOPES:
            await self._app(scope, receive, send)
            return
        client = scope.get("client")
        address = str(client[0]) if client else ""
        if self._limiter.allow_address(address):
            await self._app(scope, receive, send)
            return
        await _refuse(scope, send, _TOO_MANY)


def _header_names_and_host(scope: Scope) -> tuple[list[str], str | None]:
    """Return every header name (lower-cased) and the first ``Host`` value, as received."""
    names: list[str] = []
    host: str | None = None
    for raw_name, raw_value in scope.get("headers", ()):
        name = bytes(raw_name).decode("latin-1").lower()
        names.append(name)
        if name == "host" and host is None:
            host = bytes(raw_value).decode("latin-1")
    return names, host


async def _refuse(scope: Scope, send: Send, status: int) -> None:
    """Refuse without a body: an HTTP status, or a WebSocket closed before it was accepted."""
    if scope["type"] == "websocket":
        # Closing before accepting makes the server answer the handshake with 403.
        await send({"type": "websocket.close", "code": _POLICY_VIOLATION, "reason": ""})
        return
    await send({"type": "http.response.start", "status": status, "headers": []})
    await send({"type": "http.response.body", "body": b""})
