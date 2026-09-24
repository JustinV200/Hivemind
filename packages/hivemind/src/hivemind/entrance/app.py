"""Build the Hive Entrance's two applications from one route table.

The Hive Entrance serves two listeners (ADR-0032): loopback, always on, and remote, only while the
Entrance is exposed and not reduced. Both are FastAPI applications built here from the same
``RouteTable``, the HTTP rows of ``hivemind.entrance.routes`` and the views of
``hivemind.entrance.streams``: the loopback application mounts every row, the remote one only the
rows that name ``REMOTE``, so a loopback-only route (approval, unlock, re-grant, revocation,
invites, reopening) on the remote listener is a 404 because it was never mounted. An authenticated
row is mounted behind the gate's dependency for its declared access; every application answers
refusals through the gate's handlers, serves the committed OpenAPI document as a static file (the
interactive documentation routes are off: they load script from a CDN) and the Observation Hive's
build when it exists, and is wrapped, outermost first, in the security headers, the loopback check
(loopback only), the per-address rate limit and the body limit. CORS is allowed for ``public_url``
alone, on the remote listener alone.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Called by the
    Entrance runtime for each listener and by ``hivemind.entrance.landing_board`` for the OpenAPI
    document. Calls into the gate, the routes, the streams and FastAPI.

Key invariants:
    - One table builds both applications; the remote one never holds a loopback-only row.
    - Every response, a server error or a refusal before routing included, carries the
      Content-Security-Policy header.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md for the two listeners.
    - hivemind.entrance.gate.spec for what each row declares.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp

from hivemind.entrance.auth.session.models import Listener
from hivemind.entrance.gate import (
    AddressLimit,
    BodyLimit,
    EntranceServices,
    ErrorBody,
    LoopbackGate,
    RouteSpec,
    RouteTable,
    SecurityHeaders,
    Switch,
    gate_for,
    get_listener,
    get_services,
    install_error_handlers,
)
from hivemind.entrance.routes import RESOURCE_ROUTES
from hivemind.entrance.streams import VIEWS

LANDING_BOARD_TITLE = "HiveMind Landing Board"  # The OpenAPI document's title.
LANDING_BOARD_VERSION = "v1"  # ADR-0034: the path version; additive changes stay within it.
OPENAPI_PATH = "/v1/openapi.json"  # Where each listener serves the committed document.
SESSION_SCHEME = "HiveSession"  # The security scheme every authenticated operation names.
# The headers a signed request carries: CORS must let the remote listener's own origin send them.
_SIGNED_HEADERS = [
    "authorization",
    "content-type",
    "x-hive-timestamp",
    "x-hive-nonce",
    "x-hive-signature",
]
_CORS_METHODS = ["GET", "POST", "DELETE"]  # Every method the table uses.
_CORS_MAX_AGE_S = 600  # A browser caches a preflight for ten minutes.
# FastAPI keeps only what precedes a form feed: nothing, so a handler's docstring (its Args and
# Returns are for maintainers) never becomes the contract's description; the summary is enough.
_NO_DESCRIPTION = "\f"
# What any operation may answer besides its success: the gate's refusals, with ErrorBody.
_REFUSALS: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorBody, "description": description}
    for status, description in (
        (401, "Authentication failed: log in again with the device key and password."),
        (403, "Refused: a capability, a step-up (step_up_required) or the device's standing."),
        (404, "Not found (or not served on this listener)."),
        (409, "The item moved on meanwhile."),
        (422, "A body or parameter is not valid."),
        (429, "Too many requests from this device or address."),
    )
}

__all__ = [
    "LANDING_BOARD_TITLE",
    "LANDING_BOARD_VERSION",
    "OPENAPI_PATH",
    "SESSION_SCHEME",
    "ListenerOptions",
    "build_fastapi",
    "build_listener_app",
    "route_table",
]


@dataclass(frozen=True, slots=True)
class ListenerOptions:
    """What one listener's application is built with besides the table and the services.

    Attributes:
        listener: Which listener.
        switches: The manifest switches that are on (the steward route's).
        openapi_document: The committed OpenAPI document's bytes, served as a static file.
        bound_port: The port the loopback listener bound, for its Host check; None remotely.
        cors_origin: ``public_url``'s origin on the remote listener; None on loopback.
        web_root: The Observation Hive's build directory, served when it exists; None for none.
    """

    listener: Listener
    switches: frozenset[Switch]
    openapi_document: bytes
    bound_port: int | None = None
    cors_origin: str | None = None
    web_root: Path | None = None


def route_table() -> RouteTable:
    """Return the one table both listeners and the OpenAPI document are built from.

    Returns:
        Every resource's HTTP rows, in registry order, and every stream view.
    """
    routes = tuple(route for resource in RESOURCE_ROUTES for route in resource)
    return RouteTable(routes=routes, sockets=VIEWS)


def build_fastapi(table: RouteTable, rows: tuple[RouteSpec, ...], sockets: bool = True) -> FastAPI:
    """Build a bare FastAPI application mounting ``rows`` (and the table's views).

    Args:
        table: The route table (its views are mounted when ``sockets`` is set).
        rows: The HTTP rows to mount, each behind its gate when it is authenticated.
        sockets: Whether to mount the table's views too.

    Returns:
        The application, its interactive documentation routes off, its error handlers on.
    """
    app = FastAPI(
        title=LANDING_BOARD_TITLE,
        version=LANDING_BOARD_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_error_handlers(app)
    for route in rows:
        _mount(app, route)
    if sockets:
        for view in table.sockets:
            app.add_api_websocket_route(view.path, view.endpoint)
    return app


def build_listener_app(
    table: RouteTable, services: EntranceServices, options: ListenerOptions
) -> ASGIApp:
    """Build one listener's application, wrapped in the gate's checks.

    Args:
        table: The route table.
        services: The Entrance's services, bound to every route through FastAPI's overrides.
        options: Which listener, its switches, document, port, CORS origin and web root.

    Returns:
        The ASGI application to serve on that listener.

    Raises:
        ValueError: A loopback application was asked for without its bound port.
    """
    rows = tuple(table.routes_for(options.listener, options.switches))
    app = build_fastapi(table, rows, sockets=False)
    for view in table.sockets_for(options.listener):
        app.add_api_websocket_route(view.path, view.endpoint)
    here = services.listeners[options.listener]
    app.dependency_overrides[get_services] = lambda: services
    app.dependency_overrides[get_listener] = lambda: here
    _serve_document(app, options.openapi_document)
    # Mounted last, so every route matches first; the build may not exist yet.
    if options.web_root is not None and options.web_root.is_dir():
        app.mount("/", StaticFiles(directory=options.web_root, html=True), name="observation")
    # CORS for public_url alone, on the remote listener alone (ADR-0033).
    if options.cors_origin is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[options.cors_origin],
            allow_methods=_CORS_METHODS,
            allow_headers=_SIGNED_HEADERS,
            max_age=_CORS_MAX_AGE_S,
        )
    return _wrapped(app, services, options)


def _mount(app: FastAPI, route: RouteSpec) -> None:
    """Mount one row: behind its gate when authenticated, documented with its declarations."""
    access = route.access
    dependencies = [Depends(gate_for(access))] if access.authenticated else []
    extra: dict[str, object] = {
        "x-hive-capability": access.capability,
        "x-hive-c2": access.c2,
        "x-hive-listeners": sorted(listener.value for listener in route.listeners),
        "x-hive-effect": route.effect.value,
    }
    if route.mounted_when is not None:
        extra["x-hive-switch"] = route.mounted_when.value
    # A public route needs no session: its security requirement is explicitly empty.
    extra["security"] = [{SESSION_SCHEME: []}] if access.authenticated else []
    app.add_api_route(
        route.path,
        route.endpoint,
        methods=[route.method],
        status_code=route.status_code,
        response_model=route.response_model,
        dependencies=dependencies,
        summary=route.summary,
        description=_NO_DESCRIPTION,
        tags=[_resource(route.path)],
        responses=_REFUSALS,
        openapi_extra=extra,
    )


def _serve_document(app: FastAPI, document: bytes) -> None:
    """Serve the committed OpenAPI document's bytes, unchanged, as a static file."""

    async def openapi_document() -> Response:
        """Answer the Landing Board's contract."""
        return Response(content=document, media_type="application/json")

    app.add_api_route(OPENAPI_PATH, openapi_document, methods=["GET"], include_in_schema=False)


def _wrapped(app: FastAPI, services: EntranceServices, options: ListenerOptions) -> ASGIApp:
    """Wrap the application in the gate's checks; the security headers go outermost."""
    wrapped: ASGIApp = BodyLimit(app)
    wrapped = AddressLimit(wrapped, services.guards.limiter)
    # Loopback alone checks Host and forwarding headers, against the port it really bound.
    if options.listener is Listener.LOOPBACK:
        if options.bound_port is None:
            raise ValueError("The loopback application needs its bound port for the Host check.")
        wrapped = LoopbackGate(wrapped, options.bound_port)
    return SecurityHeaders(wrapped)


def _resource(path: str) -> str:
    """Name the resource a path belongs to (its first segment under /v1/), for the tag."""
    return path.removeprefix("/v1/").split("/", 1)[0]
