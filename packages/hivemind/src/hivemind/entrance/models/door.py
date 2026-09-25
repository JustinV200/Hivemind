"""Define the entrance resource's bodies: the door's mode, invites, approvals and held requests.

The ``/v1/entrance`` routes administer the Hive Entrance itself (ADR-0041): its mode (``OPEN`` or
``REDUCED``), reducing and reopening it, minting invites, approving or denying the devices asking
to join (loopback only, or a steward device after full step-up when the manifest allows), and the
requests held for a person's confirmation. An invite's code is answered once, to the loopback
session that minted it, and never again; a held request's payload is shown so the person confirming
knows what they are confirming, which makes the list ``C2``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by
    ``hivemind.entrance.routes.entrance``; published in the OpenAPI document. Calls into the
    enrolment and confirmation models and pydantic.

Key invariants:
    - Only ``InviteView`` carries a secret (the invite code), and only on loopback.

See Also:
    - hivemind.entrance.enrol and hivemind.entrance.auth.confirm for the flows.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hivemind.entrance.auth.confirm import PendingConfirmation, PendingStatus
from hivemind.entrance.auth.step_up import ActionKind
from hivemind.entrance.enrol.models import (
    MAX_CAPABILITIES,
    MAX_DEVICE_NAME_CHARS,
    CapabilityText,
    DisplayText,
)
from hivemind.entrance.reducer import EntranceMode
from waggle.messages.base import DeviceIdField, UtcDatetime

MAX_REASON_CHARS = 500  # Why the operator denied a request: a sentence or two.

_CONFIG = ConfigDict(frozen=True, extra="forbid")  # Every body here: immutable, no strays.

__all__ = [
    "ApprovalBody",
    "ChangedView",
    "ConfirmationList",
    "ConfirmationView",
    "ConfirmedView",
    "DenyBody",
    "InviteRequest",
    "InviteView",
    "ModeView",
    "confirmation_view",
]


class ModeView(BaseModel):
    """The Entrance's mode."""

    model_config = _CONFIG

    mode: EntranceMode = Field(description="OPEN, or REDUCED (loopback only until reopened).")
    exposed: bool = Field(description="The manifest asks for a remote listener at all.")
    remote_listening: bool = Field(description="The remote listener is serving right now.")


class ChangedView(BaseModel):
    """What a reduction or a reopening did."""

    model_config = _CONFIG

    changed: bool = Field(description="True when this call moved the mode.")
    mode: EntranceMode = Field(description="The mode now.")


class InviteRequest(BaseModel):
    """Mint an invite for a device to join (loopback only)."""

    model_config = _CONFIG

    label: DisplayText = Field(
        min_length=1,
        max_length=MAX_DEVICE_NAME_CHARS,
        description="What the operator calls the device, e.g. phone; its name until approval.",
    )


class InviteView(BaseModel):
    """A minted invite, shown once: the code, its link and its QR code."""

    model_config = _CONFIG

    device_id: DeviceIdField = Field(description="The INVITED record the invite admits.")
    code: str = Field(description="The single-use code, grouped for reading aloud.")
    url: str = Field(description="The enrolment link, with the code in its fragment.")
    qr_svg: str = Field(description="The link as an inline SVG QR code.")
    expires_at: datetime = Field(description="When it stops admitting (invite_ttl_minutes).")


class ApprovalBody(BaseModel):
    """Approve a pending device (loopback only; a steward device through its own route)."""

    model_config = _CONFIG

    name: DisplayText = Field(
        min_length=1, max_length=MAX_DEVICE_NAME_CHARS, description="Its name from now on."
    )
    capabilities: list[CapabilityText] | None = Field(
        default=None,
        max_length=MAX_CAPABILITIES,
        description="What it may do, inside the device ceiling; null grants the device role's "
        "proposed set.",
    )
    spend_cap_usd_per_day: float = Field(
        ge=0, allow_inf_nan=False, description="The most it may spend per day, in USD."
    )
    expires_at: UtcDatetime | None = Field(default=None, description="When the approval lapses.")
    interactive: bool = Field(
        default=False,
        description="For an Ed25519 program key: a person types the password at it.",
    )


class DenyBody(BaseModel):
    """Deny a pending device (loopback only)."""

    model_config = _CONFIG

    reason: str = Field(
        default="denied by the operator",
        min_length=1,
        max_length=MAX_REASON_CHARS,
        description="Why; recorded as a category on the trail.",
    )


class ConfirmationView(BaseModel):
    """A request held for a person's confirmation (C2: its payload is shown)."""

    model_config = _CONFIG

    id: str = Field(description="The pending confirmation's id (pend_...).")
    device_id: DeviceIdField = Field(description="The device that asked, which cannot step up.")
    action: ActionKind = Field(description="What it asked for, e.g. goal or new_network.")
    payload: dict[str, JsonValue] = Field(description="What it asked for, in full.")
    status: PendingStatus = Field(description="PENDING, CONFIRMED, EXPIRED or CANCELLED.")
    created_at: datetime = Field(description="When it was held.")
    expires_at: datetime = Field(description="When it can no longer be confirmed.")


class ConfirmationList(BaseModel):
    """Every request still held."""

    model_config = _CONFIG

    confirmations: list[ConfirmationView] = Field(description="The held requests, oldest first.")


class ConfirmedView(BaseModel):
    """What confirming a held request carried out."""

    model_config = _CONFIG

    pending_id: str = Field(description="The confirmation.")
    action: ActionKind = Field(description="What was carried out.")
    goal_request_id: str | None = Field(
        default=None, description="For a held goal: the goal request now committed."
    )


def confirmation_view(pending: PendingConfirmation) -> ConfirmationView:
    """Shape a held request for the person who may confirm it.

    Args:
        pending: The confirmation as the pending table holds it.

    Returns:
        Its view, payload included.
    """
    return ConfirmationView(
        id=pending.id,
        device_id=pending.device_id,
        action=pending.action,
        payload=dict(pending.payload),
        status=pending.status,
        created_at=pending.created_at,
        expires_at=pending.expires_at,
    )
