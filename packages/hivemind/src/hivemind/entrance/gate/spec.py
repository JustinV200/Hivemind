"""Define the route table's rows: what every HTTP route and WebSocket view declares about itself.

The Hive Entrance (the Hive's one HTTP door) serves two listeners from one table (ADR-0032): every
row names its method and path, the listeners that serve it (``LOOPBACK`` alone, or ``LOOPBACK`` and
``REMOTE``), who may call it (``Access``: no session at all, or a session whose device holds a
capability, plus ``honey:clearance:c2`` for routes that return personal content) and what it
changes (``RouteEffect``). The loopback application mounts every row and the remote one only rows
that name ``REMOTE``, so a loopback-only route on the remote listener is a 404 because it was never
mounted. ``RouteSpec`` is an HTTP route, ``SocketSpec`` a WebSocket view; ``RouteTable`` is the one
table both applications and the Landing Board's OpenAPI document are built from.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Rows are declared
    by ``hivemind.entrance.routes`` (one module per resource) and ``hivemind.entrance.streams`` (one
    view per stream); read by ``hivemind.entrance.app`` and ``hivemind.entrance.landing_board``.
    Calls into the session model (``Listener``) and pydantic only.

Key invariants:
    - Every row declares its listeners and its access; neither has a default.
    - A public row (no session) names no capability; the loopback set is never empty.
    - Paths live under ``/v1/``; a method is an upper-case HTTP method.

See Also:
    - docs/adr/0032-hive-entrance-http-websocket-api-and-human-inbox.md, "Two listeners are two
      applications built from one route table".
    - .claude/codingrules.md 8.11 for the route test the effects serve.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum

from pydantic import BaseModel

from hivemind.entrance.auth.session.models import Listener

API_PREFIX = "/v1/"  # ADR-0034: every route lives under /v1/; a breaking change is /v2/.
LOOPBACK_ONLY = frozenset({Listener.LOOPBACK})  # Approval, unlock, widening, invites, reopening.
BOTH_LISTENERS = frozenset({Listener.LOOPBACK, Listener.REMOTE})  # Everything else.
C2_CAPABILITY = "honey:clearance:c2"  # What a route returning personal content also needs.
_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})  # The methods a row may name.

__all__ = [
    "API_PREFIX",
    "BOTH_LISTENERS",
    "C2_CAPABILITY",
    "LOOPBACK_ONLY",
    "PUBLIC",
    "Access",
    "Endpoint",
    "RouteEffect",
    "RouteSpec",
    "RouteTable",
    "SocketSpec",
    "Switch",
    "session_with",
]

Endpoint = Callable[..., Awaitable[object]]  # A FastAPI endpoint: its signature is its contract.


class RouteEffect(Enum):
    """What a route changes; codingrules 8.11's route test reads it beside the method."""

    READ = "read"  # Reads stores; changes nothing.
    SESSION = "session"  # The caller's own session or enrolment: login, logout, step-up, redeem.
    INBOX = "inbox"  # A write into the Queen's inbox: a goal, a chat message, an answer.
    PUSH = "push"  # The caller's own push subscriptions.
    DOOR = "door"  # The Entrance itself: invites, approvals, locks, the mode, held requests.


class Switch(Enum):
    """A manifest switch a row is mounted under; a row without one is always mounted."""

    STEWARD_DEVICES = "steward_devices"  # [entrance] steward_devices: the remote steward route.


@dataclass(frozen=True, slots=True)
class Access:
    """Who may call a route.

    Attributes:
        authenticated: False for a public route (enrolment, the login ceremony); a public route
            is still limited per address.
        capability: The capability the session's device must hold, checked at the Entrance route
            enforcement point; None for a public route, or one any approved device may call.
        c2: The route returns personal (``C2``) content, so ``honey:clearance:c2`` is needed too.
        step_up_exempt: Served even while the travel lock owes the session a step-up (the step-up
            routes themselves, logout).
    """

    authenticated: bool
    capability: str | None
    c2: bool = False
    step_up_exempt: bool = False

    def __post_init__(self) -> None:
        """Refuse a public route that names a capability or personal content."""
        if not self.authenticated and (self.capability is not None or self.c2):
            raise ValueError("A public route names no capability and returns no C2 content.")

    def needs(self) -> tuple[str, ...]:
        """Return every capability a device must hold, in the order they are checked.

        Returns:
            The route's capability (if any), then ``honey:clearance:c2`` for C2 content.
        """
        needed = [self.capability] if self.capability is not None else []
        if self.c2:
            needed.append(C2_CAPABILITY)
        return tuple(needed)


PUBLIC = Access(authenticated=False, capability=None)  # No session: enrolment, login.


def session_with(capability: str | None, *, c2: bool = False) -> Access:
    """Return the access of a route a session whose device holds ``capability`` may call.

    Args:
        capability: The capability needed; None for any approved device.
        c2: The route returns C2 content.

    Returns:
        The access.
    """
    return Access(authenticated=True, capability=capability, c2=c2)


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """One HTTP route of the Landing Board.

    Attributes:
        method: The HTTP method, upper case.
        path: The path under ``/v1/``, with ``{name}`` path parameters.
        listeners: The listeners that serve it.
        access: Who may call it.
        effect: What it changes.
        endpoint: The FastAPI endpoint; thin: validate, authorise, call a subsystem, shape.
        summary: One line for the OpenAPI document.
        status_code: The success status.
        response_model: The response body's model; None for an empty body.
        mounted_when: The manifest switch it needs, or None.
    """

    method: str
    path: str
    listeners: frozenset[Listener]
    access: Access
    effect: RouteEffect
    endpoint: Endpoint = field(repr=False)
    summary: str
    status_code: int = 200
    response_model: type[BaseModel] | None = None
    mounted_when: Switch | None = None

    def __post_init__(self) -> None:
        """Refuse a row outside ``/v1/``, with an unknown method, or served nowhere on loopback."""
        _check_row(self.path, self.listeners)
        if self.method not in _METHODS:
            raise ValueError(f"{self.method!r} is not an HTTP method a route may use.")

    @property
    def is_mutating(self) -> bool:
        """Whether the method can change something (anything but GET).

        Returns:
            True for POST, PUT, PATCH and DELETE.
        """
        return self.method != "GET"


@dataclass(frozen=True, slots=True)
class SocketSpec:
    """One WebSocket view: a live stream a client subscribes to instead of polling.

    Attributes:
        path: The socket's path under ``/v1/``.
        listeners: The listeners that serve it.
        access: Who may subscribe; always a session (the first frame authenticates it).
        endpoint: The FastAPI WebSocket endpoint.
        summary: One line for the Landing Board's ``x-hive-streams``.
        frame_model: The model of every frame the view sends.
    """

    path: str
    listeners: frozenset[Listener]
    access: Access
    endpoint: Endpoint = field(repr=False)
    summary: str
    frame_model: type[BaseModel]

    def __post_init__(self) -> None:
        """Refuse a view outside ``/v1/``, served nowhere on loopback, or without a session."""
        _check_row(self.path, self.listeners)
        if not self.access.authenticated:
            raise ValueError(f"Stream {self.path} must authenticate its first frame.")


@dataclass(frozen=True, slots=True)
class RouteTable:
    """Every route and view the Entrance serves: the one table both listeners are built from.

    Attributes:
        routes: The HTTP routes.
        sockets: The WebSocket views.
    """

    routes: tuple[RouteSpec, ...]
    sockets: tuple[SocketSpec, ...]

    def __post_init__(self) -> None:
        """Refuse two rows for one method and path: the second would never be reached."""
        keys = [(route.method, route.path) for route in self.routes]
        keys += [("WEBSOCKET", socket.path) for socket in self.sockets]
        if len(keys) != len(set(keys)):
            raise ValueError("The route table names one method and path twice.")

    def routes_for(self, listener: Listener, switches: frozenset[Switch]) -> Iterator[RouteSpec]:
        """Yield the routes one listener mounts.

        Args:
            listener: The listener being built.
            switches: The manifest switches that are on.

        Yields:
            Every route that names ``listener`` and whose switch, if any, is on.
        """
        for route in self.routes:
            switched_on = route.mounted_when is None or route.mounted_when in switches
            if listener in route.listeners and switched_on:
                yield route

    def sockets_for(self, listener: Listener) -> Iterator[SocketSpec]:
        """Yield the views one listener mounts.

        Args:
            listener: The listener being built.

        Yields:
            Every view that names ``listener``.
        """
        yield from (socket for socket in self.sockets if listener in socket.listeners)


def _check_row(path: str, listeners: frozenset[Listener]) -> None:
    """Refuse a path outside ``/v1/`` or a row the loopback listener does not serve."""
    if not path.startswith(API_PREFIX):
        raise ValueError(f"{path!r} is not under {API_PREFIX}.")
    # Loopback is the Hive Stand's own door: it serves everything the remote listener does.
    if Listener.LOOPBACK not in listeners:
        raise ValueError(f"{path!r} must be served on the loopback listener.")
