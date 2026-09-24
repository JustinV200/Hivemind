"""Serve the enrol resource: redeem an invite with a device's own key (unauthenticated).

A device joins the Hive by redeeming the single-use invite the operator minted on loopback
(ADR-0033). These routes need no session, on either listener: a program sends its Ed25519 public
key and a signature over ``enrol_string``; a browser first asks for WebAuthn creation options for
the code, then sends its registration. The request then waits, PENDING, for the operator at the
Hive Stand, and every approved device is told that a device is asking to join. Every refusal is the
same answer (an unknown, used or expired code and a bad proof read alike), recorded on the trail by
reason; the per-address rate limit is what bounds guessing. The relying party a passkey is created
for is the arrival listener's: ``localhost`` on loopback, the exposure plan's name remotely.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    the route table by ``hivemind.entrance.routes``. Calls into ``hivemind.entrance.enrol``.

Key invariants:
    - No answer echoes the code; a refusal never says which check failed.

See Also:
    - hivemind.entrance.enrol.redeem for the flow.
    - hivemind.entrance.models.enrol for the bodies.
"""

from __future__ import annotations

import json

from pydantic import JsonValue, TypeAdapter

from hivemind.entrance.enrol import Ed25519Proof, Redemption, passkey_options
from hivemind.entrance.enrol import redeem_ed25519 as redeem_ed25519_flow
from hivemind.entrance.enrol import redeem_passkey as redeem_passkey_flow
from hivemind.entrance.gate.params import ArrivalParam, Here
from hivemind.entrance.gate.spec import BOTH_LISTENERS, PUBLIC, RouteEffect, RouteSpec
from hivemind.entrance.models import (
    Ed25519Redemption,
    PasskeyOptionsRequest,
    PasskeyOptionsView,
    PasskeyRedemption,
    RedemptionView,
)

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])  # WebAuthn options arrive as JSON text.

__all__ = ["ROUTES"]


async def creation_options(
    body: PasskeyOptionsRequest, here: Here, arrival: ArrivalParam
) -> PasskeyOptionsView:
    """Answer WebAuthn creation options for an invite, bound to this listener's relying party.

    Args:
        body: The invite code.
        here: This listener's enrolment dependencies.
        arrival: Where the request came from, for the trail.

    Returns:
        The options for navigator.credentials.create.
    """
    options = await passkey_options(here.enrolment, body.code, arrival.address)
    return PasskeyOptionsView(options=_JSON_OBJECT.validate_json(options))


async def redeem_ed25519(
    body: Ed25519Redemption, here: Here, arrival: ArrivalParam
) -> RedemptionView:
    """Redeem an invite with a program's Ed25519 key.

    Args:
        body: The code, the key, its signature and the device's self-description.
        here: This listener's enrolment dependencies.
        arrival: Where the request came from, for the trail.

    Returns:
        The pending device, its fingerprint and the Hive's public key.
    """
    proof = Ed25519Proof(body.public_key_hex, body.signature)
    redemption = await redeem_ed25519_flow(
        here.enrolment, body.code, proof, body.description, arrival.address
    )
    return _redemption_view(redemption)


async def redeem_passkey(
    body: PasskeyRedemption, here: Here, arrival: ArrivalParam
) -> RedemptionView:
    """Redeem an invite with a browser's new passkey.

    Args:
        body: The code, the registration and the device's self-description.
        here: This listener's enrolment dependencies.
        arrival: Where the request came from, for the trail.

    Returns:
        The pending device, its fingerprint and the Hive's public key.
    """
    registration = json.dumps(body.registration)
    redemption = await redeem_passkey_flow(
        here.enrolment, body.code, registration, body.description, arrival.address
    )
    return _redemption_view(redemption)


def _redemption_view(redemption: Redemption) -> RedemptionView:
    """Shape a redemption for the device that made it."""
    return RedemptionView(
        device_id=redemption.device_id,
        fingerprint=redemption.fingerprint,
        hive_public_key_hex=redemption.hive_public_key_hex,
    )


ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path="/v1/enrol/passkey-options",
        listeners=BOTH_LISTENERS,
        access=PUBLIC,
        effect=RouteEffect.SESSION,
        endpoint=creation_options,
        summary="Get WebAuthn creation options for an invite.",
        response_model=PasskeyOptionsView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/enrol/ed25519",
        listeners=BOTH_LISTENERS,
        access=PUBLIC,
        effect=RouteEffect.SESSION,
        endpoint=redeem_ed25519,
        summary="Redeem an invite with an Ed25519 key; the device then waits for approval.",
        status_code=202,
        response_model=RedemptionView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/enrol/passkey",
        listeners=BOTH_LISTENERS,
        access=PUBLIC,
        effect=RouteEffect.SESSION,
        endpoint=redeem_passkey,
        summary="Redeem an invite with a new passkey; the device then waits for approval.",
        status_code=202,
        response_model=RedemptionView,
    ),
)
