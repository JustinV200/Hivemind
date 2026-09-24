"""Serve the devices resource: list devices, read your own, lock, unlock, revoke, re-grant.

Every client is an enrolled device (ADR-0033). ``GET /v1/devices`` lists them (``observe``);
``GET /v1/devices/me`` is any session's own record. Locking is narrowing, so an interactive device
may lock another from either listener after step-up (a lost phone), and only loopback unlocks.
Unlocking, revoking (optionally cancelling the device's open goals in the same step, the rest named
in the answer) and re-granting what an approved device may do (after step-up: a capability change)
exist only on the loopback listener, so the remote application never mounts them. A revocation
also rebuilds the remote listener's mutual-TLS revocation list.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    the route table. Calls into ``hivemind.entrance.enrol`` (standing and re-grant) and the gate's
    step-up.

Key invariants:
    - Unlock, revoke and re-grant are loopback-only rows; lock needs a stepped-up interactive
      session wherever it arrives.
    - Every change is an enrolment flow's, recorded with its ``guard.entrance_*`` event.

See Also:
    - hivemind.entrance.enrol.standing and .decisions for the flows.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from hivemind.entrance.auth.step_up import ActionKind
from hivemind.entrance.enrol import GrantChange, LockReason, lock, regrant, revoke, unlock
from hivemind.entrance.gate.params import CallerParam, Services
from hivemind.entrance.gate.spec import (
    BOTH_LISTENERS,
    LOOPBACK_ONLY,
    Access,
    RouteEffect,
    RouteSpec,
    session_with,
)
from hivemind.entrance.gate.step_up import require_step_up
from hivemind.entrance.models import (
    DeviceList,
    DeviceView,
    RevocationView,
    RevokeBody,
    WidenBody,
    device_view,
)
from waggle.messages.base import DeviceIdField

OBSERVE = "observe"  # Listing devices is a read-only view.
SUBMIT = "entrance:submit"  # A device that acts for the operator may narrow the door.
STEWARD = "entrance:steward"  # Administering devices on loopback.

DeviceIdPath = Annotated[DeviceIdField, Path(description="The device's id.")]

__all__ = ["ROUTES"]


async def list_devices(services: Services) -> DeviceList:
    """List every enrolled device, oldest first.

    Args:
        services: The Entrance's services (the Entrance tables).

    Returns:
        Every device's view.
    """
    # Latency: one local read of the Entrance tables.
    devices = await services.enrolment.records.store.list_devices()
    return DeviceList(devices=[device_view(device) for device in devices])


async def read_own_device(caller: CallerParam) -> DeviceView:
    """Read the calling session's own device record.

    Args:
        caller: The admitted caller.

    Returns:
        Its view, as the request was admitted.
    """
    return device_view(caller.device)


async def lock_device(
    device_id: DeviceIdPath, caller: CallerParam, services: Services
) -> DeviceView:
    """Lock another device (a lost phone): an interactive device, after step-up.

    Args:
        device_id: The device to lock.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The device, LOCKED; its sessions ended and its sockets closed.
    """
    await require_step_up(services, caller, ActionKind.LOCK_DEVICE)
    locked = await lock(services.enrolment, device_id, caller.device.id, LockReason.REMOTE_LOCK)
    return device_view(locked)


async def unlock_device(
    device_id: DeviceIdPath, caller: CallerParam, services: Services
) -> DeviceView:
    """Unlock a locked device (loopback only).

    Args:
        device_id: The LOCKED device.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The device, APPROVED again.
    """
    return device_view(await unlock(services.enrolment, device_id, caller.device.id))


async def revoke_device(
    device_id: DeviceIdPath, body: RevokeBody, caller: CallerParam, services: Services
) -> RevocationView:
    """Revoke a device (loopback only), optionally cancelling its open goals in the same step.

    Args:
        device_id: The device.
        body: Whether to cancel its open goals.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The device, REVOKED, and what became of its goals.
    """
    revocation = await revoke(services.enrolment, device_id, caller.device.id, body.cancel_goals)
    # Its client certificate stops working at the next handshake (mutual TLS modes).
    await services.door.device_revoked(device_id)
    return RevocationView(
        device=device_view(revocation.device),
        goals_cancelled=list(revocation.goals_cancelled),
        goals_left_running=list(revocation.goals_left_running),
    )


async def regrant_device(
    device_id: DeviceIdPath, body: WidenBody, caller: CallerParam, services: Services
) -> DeviceView:
    """Replace what an approved device may do (loopback only, after step-up).

    Args:
        device_id: The APPROVED device.
        body: Its whole new set, and a new daily cap.
        caller: The admitted caller.
        services: The Entrance's services.

    Returns:
        The device with its new grant.
    """
    await require_step_up(services, caller, ActionKind.CAPABILITY_CHANGE)
    change = GrantChange(
        capabilities=tuple(body.capabilities),
        spend_cap_usd_per_day=body.spend_cap_usd_per_day,
        actor=caller.device.id,
    )
    return device_view(await regrant(services.enrolment, device_id, change))


_STEWARD = session_with(STEWARD)
_DEVICE = "/v1/devices/{device_id}"

ROUTES: tuple[RouteSpec, ...] = (
    RouteSpec(
        method="GET",
        path="/v1/devices",
        listeners=BOTH_LISTENERS,
        access=session_with(OBSERVE),
        effect=RouteEffect.READ,
        endpoint=list_devices,
        summary="List every enrolled device.",
        response_model=DeviceList,
    ),
    RouteSpec(
        method="GET",
        path="/v1/devices/me",
        listeners=BOTH_LISTENERS,
        access=Access(authenticated=True, capability=None),
        effect=RouteEffect.READ,
        endpoint=read_own_device,
        summary="Read the calling device's own record.",
        response_model=DeviceView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_DEVICE}/lock",
        listeners=BOTH_LISTENERS,
        access=session_with(SUBMIT),
        effect=RouteEffect.DOOR,
        endpoint=lock_device,
        summary="Lock another device (interactive, after step-up); only loopback unlocks it.",
        response_model=DeviceView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_DEVICE}/unlock",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=unlock_device,
        summary="Unlock a locked device (loopback only).",
        response_model=DeviceView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_DEVICE}/revoke",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=revoke_device,
        summary="Revoke a device, optionally cancelling its open goals (loopback only).",
        response_model=RevocationView,
    ),
    RouteSpec(
        method="POST",
        path=f"{_DEVICE}/capabilities",
        listeners=LOOPBACK_ONLY,
        access=_STEWARD,
        effect=RouteEffect.DOOR,
        endpoint=regrant_device,
        summary="Replace what a device may do (loopback only, after step-up).",
        response_model=DeviceView,
    ),
)
