"""Serve the auth resource: the login ceremony, logout, and step-up on an open session.

Login is two unauthenticated calls on either listener (ADR-0033): ``POST /v1/auth/challenge`` issues
a single-use, 60-second challenge for a device (a browser registers its session's WebCrypto key in
the same call), and ``POST /v1/auth/login`` answers it with the device's proof and the operator
password, the proof checked first; the session opened is bound to the device's key and to the
listener it arrived on, and its token is answered once. Logout ends the session and closes its
sockets. Step-up repeats the ceremony on an open session from an interactive device and keeps it
stepped up for ``step_up_window_minutes``. Each handler is thin: the flows in
``hivemind.entrance.auth`` decide; every refusal is the same ``401``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    the route table by ``hivemind.entrance.routes``; each listener picks its own relying party
    through ``Here``. Calls into ``hivemind.entrance.auth`` (login, step-up, the session book).

Key invariants:
    - The password never leaves its ``SecretStr`` except into the flow that checks it.
    - A token is answered once, by login, and never logged.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for the ceremony.
    - hivemind.entrance.models.auth for the bodies.
"""

from __future__ import annotations

import json

from pydantic import JsonValue, TypeAdapter

from hivemind.entrance.auth.login import DeviceProof, LoginChallenge, begin_login, finish_login
from hivemind.entrance.auth.step_up import step_up, step_up_challenge
from hivemind.entrance.gate.params import ArrivalParam, CallerParam, Here, Services
from hivemind.entrance.gate.spec import (
    BOTH_LISTENERS,
    PUBLIC,
    Access,
    RouteEffect,
    RouteSpec,
)
from hivemind.entrance.models import (
    ChallengeRequest,
    ChallengeView,
    LoginRequest,
    OpenedSessionView,
    SteppedUpView,
    StepUpRequest,
)

# Any approved device may manage its own session, even while the travel lock owes a step-up.
_OWN_SESSION = Access(authenticated=True, capability=None, step_up_exempt=True)
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])  # WebAuthn options arrive as JSON text.

__all__ = ["ROUTES"]


async def login_challenge(
    body: ChallengeRequest, here: Here, arrival: ArrivalParam
) -> ChallengeView:
    """Issue a login challenge for a device.

    Args:
        body: The device, and a browser's session key.
        here: This listener's login dependencies (its relying party).
        arrival: The listener and address the request came from.

    Returns:
        The challenge, and passkey request options for a passkey device.
    """
    challenge = await begin_login(here.auth, body.device_id, arrival, body.binding_key)
    return _challenge_view(challenge)


async def login(body: LoginRequest, here: Here, arrival: ArrivalParam) -> OpenedSessionView:
    """Answer a login challenge; open a session bound to the device's key.

    Args:
        body: The proof, and the operator password.
        here: This listener's login dependencies.
        arrival: The listener and address the request came from.

    Returns:
        The session and its token, answered this once.
    """
    proof = DeviceProof(
        body.device_id,
        body.nonce,
        signature=body.signature,
        assertion=json.dumps(body.assertion) if body.assertion is not None else None,
        binding_key=body.binding_key,
    )
    opened = await finish_login(here.auth, proof, body.password.get_secret_value(), arrival)
    session = opened.session
    return OpenedSessionView(
        token=opened.token,
        device_id=session.device_id,
        listener=session.listener.value,
        expires_at=session.expires_at,
        needs_step_up=session.needs_step_up,
    )


async def logout(caller: CallerParam, here: Here, services: Services) -> None:
    """End the calling session and close every socket it opened.

    Args:
        caller: The admitted caller.
        here: This listener's session book.
        services: The Entrance's services (the socket registry).
    """
    await here.auth.sessions.logout(caller.session.session)
    services.streams.sockets.close_session(caller.session.session.token_hash)


async def new_step_up_challenge(caller: CallerParam, here: Here) -> ChallengeView:
    """Issue a step-up challenge bound to the calling session (interactive devices only).

    Args:
        caller: The admitted caller.
        here: This listener's dependencies.

    Returns:
        The challenge.
    """
    return _challenge_view(step_up_challenge(here.auth, caller.session))


async def answer_step_up(body: StepUpRequest, caller: CallerParam, here: Here) -> SteppedUpView:
    """Re-run the device's factors on the calling session; keep it stepped up for the window.

    Args:
        body: The fresh proof, and the password for an Ed25519 device.
        caller: The admitted caller.
        here: This listener's dependencies.

    Returns:
        When the step-up window closes.
    """
    proof = DeviceProof(
        caller.device.id,
        body.nonce,
        signature=body.signature,
        assertion=json.dumps(body.assertion) if body.assertion is not None else None,
    )
    password = body.password.get_secret_value() if body.password is not None else None
    session = await step_up(here.auth, caller.session, proof, password, caller.arrival)
    until = session.stepped_up_until
    # step_up sets the window on the row it returns; a missing one is a flow bug, not a client's.
    if until is None:
        raise ValueError(f"Session of device {caller.device.id} came back without a window.")
    return SteppedUpView(stepped_up_until=until)


def _challenge_view(challenge: LoginChallenge) -> ChallengeView:
    """Shape a challenge, parsing a passkey device's request options into an object."""
    options = challenge.passkey_options
    return ChallengeView(
        device_id=challenge.device_id,
        nonce=challenge.nonce,
        expires_at=challenge.expires_at,
        passkey_options=_JSON_OBJECT.validate_json(options) if options is not None else None,
    )


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path="/v1/auth/challenge",
        listeners=BOTH_LISTENERS,
        access=PUBLIC,
        effect=RouteEffect.SESSION,
        endpoint=login_challenge,
        summary="Issue a login challenge for a device.",
        response_model=ChallengeView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/auth/login",
        listeners=BOTH_LISTENERS,
        access=PUBLIC,
        effect=RouteEffect.SESSION,
        endpoint=login,
        summary="Log in with the device's proof and the operator password.",
        status_code=201,
        response_model=OpenedSessionView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/auth/logout",
        listeners=BOTH_LISTENERS,
        access=_OWN_SESSION,
        effect=RouteEffect.SESSION,
        endpoint=logout,
        summary="End the calling session and close its sockets.",
        status_code=204,
    ),
    RouteSpec(
        method="POST",
        path="/v1/auth/step-up/challenge",
        listeners=BOTH_LISTENERS,
        access=_OWN_SESSION,
        effect=RouteEffect.SESSION,
        endpoint=new_step_up_challenge,
        summary="Issue a step-up challenge bound to the calling session.",
        response_model=ChallengeView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/auth/step-up",
        listeners=BOTH_LISTENERS,
        access=_OWN_SESSION,
        effect=RouteEffect.SESSION,
        endpoint=answer_step_up,
        summary="Step the calling session up: both factors again, from an interactive device.",
        response_model=SteppedUpView,
    ),
)
