"""Admit a request: authenticate its session, then check its rate, its step-up and its capability.

Every authenticated Landing Board route depends on this module's gate (ADR-0033): the request is
authenticated over the raw method, path and query exactly as sent, the headers and the body's
SHA-256 (``authenticate_request``, which also refuses a session opened on the other listener); its
device is held to ``rate_limit_per_device``; a session the travel lock flagged must step up before
anything else; and each capability the route declares is checked at the guard's Entrance route
enforcement point, over the device's approved set, where a refusal is recorded as ``guard.denied``
and counts toward the denial-burst lock. The admitted request becomes a ``Caller`` the route reads
through ``current_caller``. A WebSocket's first frame goes through the same ``police`` step.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.gate``. Used as a route
    dependency by ``hivemind.entrance.app`` and by ``hivemind.entrance.streams`` for sockets. Calls
    into the session checks, the confirmation flow (a device that cannot step up), the guard's
    ``Enforcer`` and the denial counter.

Key invariants:
    - The signature is checked over the request exactly as it arrived; nothing is re-serialised.
    - A capability is never granted here: only the Guard's decision over the device's own set.
    - No header value, token, signature or body reaches a log line or an error.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the checks.
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the point.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.auth.confirm import PendingStatus, hold
from hivemind.entrance.auth.session import (
    Arrival,
    AuthenticatedSession,
    Listener,
    SignedRequest,
    authenticate_request,
)
from hivemind.entrance.auth.step_up import ActionKind, StepUpReason
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.gate.errors import (
    CapabilityDeniedError,
    RateLimitedError,
    StepUpRequiredError,
)
from hivemind.entrance.gate.services import (
    EntranceServices,
    ListenerDeps,
    get_listener,
    get_services,
)
from hivemind.entrance.gate.spec import Access
from hivemind.guard import (
    Capability,
    EnforcementPoint,
    PolicyRequest,
    PrincipalKind,
    PrincipalRef,
)
from hivemind.guard.policy import DEVICE_ROLE

CALLER_KEY = "hivemind.entrance.caller"  # Where the gate leaves the admitted Caller in the scope.
UNKNOWN_ADDRESS = "unknown"  # A peer the server could not name; the trail records a placeholder.

__all__ = [
    "CALLER_KEY",
    "Caller",
    "arrival_of",
    "authorise",
    "current_arrival",
    "current_caller",
    "gate_for",
    "police",
]


@dataclass(frozen=True, slots=True)
class Caller:
    """An admitted request: its session and how it arrived.

    Attributes:
        session: The authenticated session, its device and that device's capabilities.
        arrival: The listener and address the request came from.
    """

    session: AuthenticatedSession
    arrival: Arrival

    @property
    def device(self) -> EnrolledDevice:
        """The session's device, APPROVED when the request was admitted."""
        return self.session.device

    @property
    def listener(self) -> Listener:
        """The listener the request arrived on."""
        return self.arrival.listener


def gate_for(access: Access) -> Callable[..., Awaitable[None]]:
    """Build the route dependency that admits a request under ``access``.

    Args:
        access: The route's declared access; it must be authenticated.

    Returns:
        A FastAPI dependency that leaves the admitted ``Caller`` in the request scope.

    Raises:
        ValueError: ``access`` is public: a public route has no gate.
    """
    if not access.authenticated:
        raise ValueError("A public route is not gated by a session.")

    async def gate(
        request: Request,
        services: Annotated[EntranceServices, Depends(get_services)],
        here: Annotated[ListenerDeps, Depends(get_listener)],
    ) -> None:
        """Admit the request or raise; the route then reads its Caller."""
        request.scope[CALLER_KEY] = await _admit(request, services, here, access)

    return gate


def current_caller(request: Request) -> Caller:
    """Return the Caller the route's gate admitted (a FastAPI dependency).

    Args:
        request: The request.

    Returns:
        The admitted Caller.

    Raises:
        InvariantViolationError: The route has no gate: a route bug, never a client's.
    """
    caller = request.scope.get(CALLER_KEY)
    if not isinstance(caller, Caller):
        raise InvariantViolationError(f"Route {request.url.path} reads a caller it never admitted.")
    return caller


def current_arrival(
    request: Request, here: Annotated[ListenerDeps, Depends(get_listener)]
) -> Arrival:
    """Return how a request arrived, for a public route (a FastAPI dependency).

    Args:
        request: The request.
        here: The listener's dependencies.

    Returns:
        The listener and the peer address.
    """
    return arrival_of(request.client.host if request.client else None, here.listener)


def arrival_of(host: str | None, listener: Listener) -> Arrival:
    """Build an Arrival from the peer address the server reported.

    Args:
        host: The peer's address, or None when the server could not name it.
        listener: The listener it arrived on.

    Returns:
        The Arrival.
    """
    return Arrival(listener, host or UNKNOWN_ADDRESS)


async def police(services: EntranceServices, caller: Caller, access: Access) -> None:
    """Hold an admitted caller to its rate, the travel lock and the route's capabilities.

    Args:
        services: The Entrance's services.
        caller: The admitted caller.
        access: The route's or view's declared access.

    Raises:
        RateLimitedError: The device is over ``rate_limit_per_device``.
        StepUpRequiredError: The travel lock owes the session a step-up.
        CapabilityDeniedError: The Guard refused a capability.
    """
    if not services.guards.limiter.allow_device(caller.device.id):
        raise RateLimitedError()
    # The travel lock's flag first: a new network steps up before anything else (ADR-0033).
    if caller.session.needs_step_up and not access.step_up_exempt:
        raise await _new_network(services, caller)
    for capability in access.needs():
        await authorise(services, caller, capability)


async def authorise(services: EntranceServices, caller: Caller, capability: str) -> None:
    """Check one capability at the Entrance route point; a refusal counts toward a burst lock.

    Args:
        services: The Entrance's services (the enforcer and the denial counter).
        caller: The admitted caller.
        capability: The capability the route needs.

    Raises:
        CapabilityDeniedError: The Guard refused it; ``guard.denied`` is already on the trail.
    """
    device = caller.device
    request = PolicyRequest(
        principal=PrincipalRef(kind=PrincipalKind.CLIENT_DEVICE, id=device.id, role=DEVICE_ROLE),
        point=EnforcementPoint.ENTRANCE_ROUTE,
        needed=Capability.parse(capability),
        held=caller.session.capabilities,
    )
    # Latency: a pure decision, plus one local trail write when it refuses.
    decision = await services.guards.enforcer.check(request)
    if decision.allowed:
        return
    # A submit-only program suddenly calling observe routes is a signal: enough of them lock it.
    await services.guards.denials.record(device.id)
    raise CapabilityDeniedError(capability)


async def _admit(
    request: Request, services: EntranceServices, here: ListenerDeps, access: Access
) -> Caller:
    """Authenticate the request exactly as it arrived, then police it."""
    scope = request.scope
    raw_path = scope.get("raw_path")
    arrival = arrival_of(request.client.host if request.client else None, here.listener)
    signed = SignedRequest(
        method=request.method,
        # The path and query exactly as sent: what the client signed, percent-encoding untouched.
        raw_path=raw_path.decode("latin-1") if isinstance(raw_path, bytes) else request.url.path,
        raw_query=bytes(scope.get("query_string", b"")).decode("latin-1"),
        # Latency: the body is already buffered by the server; FastAPI reuses this read.
        body=await request.body(),
        headers=request.headers,
        arrival=arrival,
    )
    # Latency: one or two local reads and one nonce write in the Entrance tables.
    session = await authenticate_request(here.auth.sessions, signed, services.clock.now())
    caller = Caller(session, arrival)
    await police(services, caller, access)
    return caller


async def _new_network(services: EntranceServices, caller: Caller) -> StepUpRequiredError:
    """Build the travel lock's refusal; a device that cannot step up gets a person to clear it."""
    if caller.session.interactive:
        return StepUpRequiredError(StepUpReason.NEW_NETWORK)
    pending_id = await _open_network_hold(services, caller)
    return StepUpRequiredError(StepUpReason.NEW_NETWORK, pending_id)


async def _open_network_hold(services: EntranceServices, caller: Caller) -> str:
    """Return the device's held NEW_NETWORK request, holding one when none waits yet."""
    pending = services.enrolment.records.store.pending
    device_id = caller.device.id
    # One hold per device and network: a program retrying must not flood the human.
    for held in await pending.list_by_status(PendingStatus.PENDING):
        same_network = held.payload.get("network") == caller.session.session.network
        if held.device_id == device_id and held.action is ActionKind.NEW_NETWORK and same_network:
            return held.id
    payload = {"network": caller.session.session.network}
    return await hold(
        services.enrolment,
        caller.device,
        ActionKind.NEW_NETWORK,
        payload,
        services.rules.confirmation_ttl,
    )
