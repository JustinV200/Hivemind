"""Hold the Entrance's gate: what every request passes before a route and what a route is handed.

Every Landing Board route and view goes through the same gate (ADR-0032, ADR-0033). ``spec`` defines
the route table's rows (method, path, listeners, access, effect); ``middleware`` wraps each
listener's application (security headers on every response, the loopback listener's Host and
forwarding-header check, the per-address rate limit); ``admit`` is the authentication dependency
(the signed request exactly as sent, the per-device rate, the travel lock, each capability at the
guard's Entrance route enforcement point, a denial counting toward the burst lock); ``step_up``
guards sensitive actions (a program's request held for a person); ``handlers`` answers every
refusal with its status and a small body; ``errors`` are the gate's own refusals; ``services`` and
``params`` are what a route is handed.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Used by
    ``hivemind.entrance.routes``, ``hivemind.entrance.streams`` and ``hivemind.entrance.app``.
    Calls into the Entrance's auth, enrolment, confirmation and push packages and
    ``hivemind.guard``.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - No header value, token, signature or body is logged or answered back.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the checks.

Public API:
    - RouteSpec, SocketSpec, RouteTable, Access, RouteEffect, Switch, PUBLIC, session_with,
      LOOPBACK_ONLY, BOTH_LISTENERS, C2_CAPABILITY, API_PREFIX, Endpoint: the table's rows (spec).
    - SecurityHeaders, LoopbackGate, AddressLimit, BodyLimit, CONTENT_SECURITY_POLICY,
      MAX_BODY_BYTES: the application wrappers (middleware).
    - Caller, gate_for, police, authorise, current_caller, current_arrival, arrival_of,
      CALLER_KEY: admission (admit).
    - require_step_up: step-up for sensitive actions (step_up).
    - ErrorBody, install_error_handlers, status_for, INVALID_REQUEST_CODE, NOT_FOUND_CODE,
      ROUTER_REFUSAL_CODE: answers (handlers).
    - StepUpRequiredError, CapabilityDeniedError, RateLimitedError: the gate's refusals (errors).
    - EntranceServices, QueenDoor, HiveReads, PushServices, GateGuards, EntranceRules,
      StreamServices, DoorControl, ListenerDeps, get_services, get_listener: the services
      (services).
    - Services, Here, CallerParam, ArrivalParam: a route's parameters (params).
"""

from hivemind.entrance.gate.admit import (
    CALLER_KEY,
    Caller,
    arrival_of,
    authorise,
    current_arrival,
    current_caller,
    gate_for,
    police,
)
from hivemind.entrance.gate.errors import (
    CapabilityDeniedError,
    RateLimitedError,
    StepUpRequiredError,
)
from hivemind.entrance.gate.handlers import (
    INVALID_REQUEST_CODE,
    NOT_FOUND_CODE,
    ROUTER_REFUSAL_CODE,
    ErrorBody,
    install_error_handlers,
    status_for,
)
from hivemind.entrance.gate.middleware import (
    CONTENT_SECURITY_POLICY,
    MAX_BODY_BYTES,
    AddressLimit,
    BodyLimit,
    LoopbackGate,
    SecurityHeaders,
)
from hivemind.entrance.gate.params import ArrivalParam, CallerParam, Here, Services
from hivemind.entrance.gate.services import (
    DoorControl,
    EntranceRules,
    EntranceServices,
    GateGuards,
    HiveReads,
    ListenerDeps,
    PushServices,
    QueenDoor,
    StreamServices,
    get_listener,
    get_services,
)
from hivemind.entrance.gate.spec import (
    API_PREFIX,
    BOTH_LISTENERS,
    C2_CAPABILITY,
    LOOPBACK_ONLY,
    PUBLIC,
    Access,
    Endpoint,
    RouteEffect,
    RouteSpec,
    RouteTable,
    SocketSpec,
    Switch,
    session_with,
)
from hivemind.entrance.gate.step_up import require_step_up

__all__ = [
    "API_PREFIX",
    "BOTH_LISTENERS",
    "C2_CAPABILITY",
    "CALLER_KEY",
    "CONTENT_SECURITY_POLICY",
    "INVALID_REQUEST_CODE",
    "LOOPBACK_ONLY",
    "MAX_BODY_BYTES",
    "NOT_FOUND_CODE",
    "PUBLIC",
    "ROUTER_REFUSAL_CODE",
    "Access",
    "AddressLimit",
    "ArrivalParam",
    "BodyLimit",
    "Caller",
    "CallerParam",
    "CapabilityDeniedError",
    "DoorControl",
    "Endpoint",
    "EntranceRules",
    "EntranceServices",
    "ErrorBody",
    "GateGuards",
    "Here",
    "HiveReads",
    "ListenerDeps",
    "LoopbackGate",
    "PushServices",
    "QueenDoor",
    "RateLimitedError",
    "RouteEffect",
    "RouteSpec",
    "RouteTable",
    "SecurityHeaders",
    "Services",
    "SocketSpec",
    "StepUpRequiredError",
    "StreamServices",
    "Switch",
    "arrival_of",
    "authorise",
    "current_arrival",
    "current_caller",
    "gate_for",
    "get_listener",
    "get_services",
    "install_error_handlers",
    "police",
    "require_step_up",
    "session_with",
    "status_for",
]
