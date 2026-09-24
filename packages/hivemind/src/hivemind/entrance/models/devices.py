"""Define the devices resource's bodies: a device as the Landing Board shows it, and its changes.

Every client of the Hive Entrance is an enrolled device (ADR-0033). ``DeviceView`` is what a device
may see of one: its name, standing, key kind and fingerprint, what it may do and spend, and the
self-description it sent at redemption, which every surface escapes because the device wrote it; it
never carries the public key or a credential id. The change bodies are the operator's decisions on
loopback: revoking (optionally cancelling the device's open goals in the same step) and widening
what an approved device may do.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.devices`` and ``.entrance``; published in the OpenAPI document.
    Calls into the enrolment model and pydantic.

Key invariants:
    - A view carries no key material beyond the fingerprint.

See Also:
    - hivemind.entrance.enrol for the device model and its state machine.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from hivemind.entrance.enrol.models import (
    MAX_CAPABILITIES,
    CapabilityText,
    DeviceDescription,
    EnrolledDevice,
)
from hivemind.entrance.enrol.state import DeviceStatus
from waggle.messages.base import DeviceIdField

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = ["DeviceList", "DeviceView", "RevocationView", "RevokeBody", "WidenBody", "device_view"]


class DeviceView(BaseModel):
    """One enrolled device, as the Landing Board shows it."""

    model_config = _CONFIG

    id: DeviceIdField = Field(description="The device's id.")
    name: str = Field(description="Its name, as the operator bound it at approval.")
    status: DeviceStatus = Field(description="INVITED, PENDING, APPROVED, LOCKED, DENIED, ...")
    key_kind: str | None = Field(description="ed25519 or passkey; null before redemption.")
    fingerprint: str | None = Field(description="Its key's fingerprint; null before redemption.")
    interactive: bool = Field(description="A person types at it, so it may step up.")
    capabilities: list[str] = Field(description="What it may do, as capability strings.")
    spend_cap_usd_per_day: float | None = Field(description="Its daily cap; null: uncapped.")
    expires_at: datetime | None = Field(description="When its current standing lapses.")
    loopback_bound: bool = Field(description="The Hive Stand console: loopback sessions only.")
    backup_eligible: bool = Field(description="A passkey that may be synced elsewhere.")
    backup_state: bool = Field(description="A passkey that was synced when registered.")
    description: DeviceDescription | None = Field(
        description="What it said about itself at redemption (display text; escape it)."
    )
    created_at: datetime = Field(description="When its record was created (invite minted).")
    approved_at: datetime | None = Field(description="When the operator approved it.")
    last_seen_at: datetime | None = Field(description="When it last authenticated.")


class DeviceList(BaseModel):
    """Every device matching a listing."""

    model_config = _CONFIG

    devices: list[DeviceView] = Field(description="The devices, oldest first.")


class RevokeBody(BaseModel):
    """Revoke a device (loopback only)."""

    model_config = _CONFIG

    cancel_goals: bool = Field(
        default=False,
        description="Also cancel the device's open goals in the same step: placed work is "
        "stopped on its Warden; a goal whose Warden cannot be reached is named as left running. "
        "Its goal requests not planned yet are refused whatever this says.",
    )


class RevocationView(BaseModel):
    """What a revocation did."""

    model_config = _CONFIG

    device: DeviceView = Field(description="The device, REVOKED.")
    goals_cancelled: list[str] = Field(description="The open goals cancelled in the same step.")
    goals_left_running: list[str] = Field(description="The open goals still running.")
    requests_refused: list[str] = Field(
        default_factory=list,
        description="Its goal requests not planned yet, refused in the same step so they never "
        "run.",
    )


class WidenBody(BaseModel):
    """Change what an approved device may do (loopback only, after step-up)."""

    model_config = _CONFIG

    capabilities: list[CapabilityText] = Field(
        max_length=MAX_CAPABILITIES,
        description="Its whole new capability set, inside the device role's ceiling.",
    )
    spend_cap_usd_per_day: float | None = Field(
        default=None, ge=0, allow_inf_nan=False, description="A new daily cap; null keeps it."
    )


def device_view(device: EnrolledDevice) -> DeviceView:
    """Shape a stored device for the Landing Board.

    Args:
        device: The record as the Entrance tables hold it.

    Returns:
        Its view: no public key, no credential id.
    """
    return DeviceView(
        id=device.id,
        name=device.name,
        status=device.status,
        key_kind=device.key_kind.value if device.key_kind is not None else None,
        fingerprint=device.fingerprint,
        interactive=device.interactive,
        capabilities=list(device.capabilities),
        spend_cap_usd_per_day=device.spend_cap_usd_per_day,
        expires_at=device.expires_at,
        loopback_bound=device.loopback_bound,
        backup_eligible=device.backup_eligible,
        backup_state=device.backup_state,
        description=device.description,
        created_at=device.created_at,
        approved_at=device.approved_at,
        last_seen_at=device.last_seen_at,
    )
