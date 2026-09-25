"""Serve enrolment's decisions: mint invites, list who asks to join, approve or deny them.

Devices are enrolled, then approved at the Hive Stand (ADR-0041). Minting and cancelling invites,
registering a device offline (its public key and certificate request, copied off a device that can
reach no enrolment listener), approving and denying pending requests exist only on the loopback
listener: the remote application never mounts them, so they answer 404 there, not 403. The loopback
approval answers the certificate issued from the device's request and, under mutual TLS, a browser's
PKCS#12 bundle with its passphrase, to the approving operator this once. The pending list is
readable from either listener by a steward, because a steward device may approve remotely through
its own route, which is mounted only when ``[entrance] steward_devices`` is on and only acts after
full step-up, granting at most its own set within the device ceiling and never stewardship itself.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.entrance``.
    Registered in the route table. Calls into ``hivemind.entrance.enrol``.

Key invariants:
    - The invite code is answered once, to the loopback session that minted it.
    - A bundle is answered once, to the loopback session that approved it, and stored nowhere.
    - The steward route is mounted only under its switch, and never grants ``entrance:steward``.

See Also:
    - hivemind.entrance.enrol for invites, decisions and the steward rule.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.entrance.auth.step_up import ActionKind
from hivemind.entrance.enrol import (
    ApprovalRequest,
    DeviceStatus,
    OfflineRegistration,
    approve,
    approve_with_bundle,
    cancel_invite,
    deny,
    device_ceiling,
    mint_invite,
    register_offline,
    steward_grant,
    steward_terms,
)
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import (
    BOTH_LISTENERS,
    LOOPBACK_ONLY,
    RouteEffect,
    RouteSpec,
    Switch,
    session_with,
)
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.models import (
    ApprovalBody,
    ApprovedDeviceView,
    DenyBody,
    DeviceList,
    DeviceView,
    InviteRequest,
    InviteView,
    RedemptionView,
    RegistrationBody,
    approved_view,
    device_view,
)
from hivemind.guard import CapabilitySet, proposed_set
from waggle.messages.base import DeviceIdField

STEWARD = "entrance:steward"  # Every enrolment decision is stewardship of the door.

DeviceIdPath = Annotated[DeviceIdField, Path(description="The device's id.")]

__all__ = ["ROUTES"]


async def mint(body: InviteRequest, caller: CallerParam, services: Services) -> InviteView:
    """Mint a single-use invite (loopback only); its code is answered this once.

    Args:
        body: What the operator calls the device.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The code, its link, its QR code and its expiry.
    """
    minted = await mint_invite(services.enrolment, body.label, caller.device.id)
    return InviteView(
        device_id=minted.device_id,
        code=minted.code,
        url=minted.url,
        qr_svg=minted.qr.svg,
        expires_at=minted.expires_at,
    )


async def register(
    body: RegistrationBody, caller: CallerParam, services: Services
) -> RedemptionView:
    """Register a device offline from its public key and certificate request (loopback only).

    Args:
        body: Its name, its Ed25519 public key and its certificate signing request.
        caller: The admitted caller, whose device is the registration's actor.
        services: The Entrance's services.

    Returns:
        The PENDING device, its fingerprint and the Hive's public key.
    """
    registration = OfflineRegistration(
        name=body.name,
        public_key_hex=body.public_key_hex,
        certificate_request=body.certificate_request,
    )
    redemption = await register_offline(services.enrolment, registration, caller.device.id)
    return RedemptionView(
        device_id=redemption.device_id,
        fingerprint=redemption.fingerprint,
        hive_public_key_hex=redemption.hive_public_key_hex,
    )


async def cancel(device_id: DeviceIdPath, caller: CallerParam, services: Services) -> DeviceView:
    """Withdraw an unredeemed invite (loopback only).

    Args:
        device_id: The INVITED device.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The device, REVOKED.
    """
    return device_view(await cancel_invite(services.enrolment, device_id, caller.device.id))


async def list_pending(services: Services) -> DeviceList:
    """List the devices waiting for a decision.

    Args:
        services: The Entrance's services.

    Returns:
        Every PENDING device, with its fingerprint and self-description.
    """
    # Latency: one local read of the Entrance tables.
    pending = await services.enrolment.records.store.list_devices(DeviceStatus.PENDING)
    return DeviceList(devices=[device_view(device) for device in pending])


async def approve_pending(
    device_id: DeviceIdPath, body: ApprovalBody, caller: CallerParam, services: Services
) -> ApprovedDeviceView:
    """Approve a pending device (loopback only), with the certificate or bundle issued for it.

    Args:
        device_id: The PENDING device.
        body: Its name, capabilities, daily cap, expiry and mode.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The device, APPROVED; its certificate's PEM when one was issued from its request, and a
        browser's bundle when one was sealed (answered this once).
    """
    request = _approval(body, None, caller.device.id)
    return approved_view(await approve_with_bundle(services.enrolment, device_id, request))


async def deny_pending(
    device_id: DeviceIdPath, body: DenyBody, caller: CallerParam, services: Services
) -> DeviceView:
    """Deny a pending device (loopback only).

    Args:
        device_id: The PENDING device.
        body: Why.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The device, DENIED.
    """
    return device_view(await deny(services.enrolment, device_id, caller.device.id, body.reason))


async def steward_approve(
    device_id: DeviceIdPath, body: ApprovalBody, caller: CallerParam, services: Services
) -> DeviceView:
    """Approve a pending device from a steward device, after full step-up.

    Args:
        device_id: The PENDING device.
        body: Its name, capabilities, daily cap, expiry and mode.
        caller: The admitted caller, a steward.
        services: The Entrance's services.

    Returns:
        The device, APPROVED with at most the steward's own set.
    """
    await require_step_up(services, caller, ActionKind.CAPABILITY_CHANGE)
    policy = services.enrolment.rules.policy
    asked = (
        CapabilitySet.parse(*body.capabilities)
        if body.capabilities is not None
        else proposed_set(policy)
    )
    # ADR-0041: at most the steward's own set, inside the ceiling, never stewardship itself.
    granted = steward_grant(caller.device, asked, device_ceiling(policy))
    # Nor more spend per day, or a longer life, than the steward's own approval (ADR-0039).
    steward_terms(caller.device, body.spend_cap_usd_per_day, body.expires_at)
    request = _approval(body, granted, caller.device.id)
    return device_view(await approve(services.enrolment, device_id, request))


def _approval(body: ApprovalBody, granted: CapabilitySet | None, actor: str) -> ApprovalRequest:
    """Build the approval the enrolment flow applies: the body's terms, and who decided."""
    if granted is not None:
        capabilities: tuple[str, ...] | None = granted.as_strings()
    elif body.capabilities is not None:
        capabilities = tuple(body.capabilities)
    else:
        capabilities = None
    return ApprovalRequest(
        name=body.name,
        capabilities=capabilities,
        spend_cap_usd_per_day=body.spend_cap_usd_per_day,
        expires_at=body.expires_at,
        interactive=body.interactive,
        actor=actor,
    )


_STEWARD = session_with(STEWARD)
_PENDING = "/v1/entrance/pending/{device_id}"

ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="POST",
        path="/v1/entrance/invites",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=mint,
        summary="Mint a single-use invite for a device (loopback only).",
        status_code=201,
        response_model=InviteView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/entrance/register",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=register,
        summary="Register a device offline from its public key and certificate request "
        "(loopback only); it then waits for approval like any other.",
        status_code=201,
        response_model=RedemptionView,
    ),
    RouteSpec(
        method="DELETE",
        path="/v1/entrance/invites/{device_id}",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=cancel,
        summary="Withdraw an unredeemed invite (loopback only).",
        response_model=DeviceView,
    ),
    RouteSpec(
        method="GET",
        path="/v1/entrance/pending",
        listeners=BOTH_LISTENERS,
        access=_STEWARD,
        effect=RouteEffect.READ,
        endpoint=list_pending,
        summary="List the devices asking to join.",
        response_model=DeviceList,
    ),
    RouteSpec(
        method="POST",
        path=f"{_PENDING}/approve",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=approve_pending,
        summary="Approve a device asking to join (loopback only), issuing its client "
        "certificate when the Hive runs its own authority.",
        response_model=ApprovedDeviceView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_PENDING}/deny",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=deny_pending,
        summary="Deny a device asking to join (loopback only).",
        response_model=DeviceView,
    ),
    RouteSpec(
        method="POST",
        path="/v1/entrance/steward/{device_id}/approve",
        listeners=BOTH_LISTENERS,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=steward_approve,
        summary="Approve a device from a steward device after full step-up "
        "(mounted only when [entrance] steward_devices is on).",
        response_model=DeviceView,
        mounted_when=Switch.STEWARD_DEVICES,
    ),
)
