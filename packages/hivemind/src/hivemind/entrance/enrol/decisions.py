"""Decide a pending request at the Hive Stand: approve it with what it may do, or deny it.

A redeemed invite leaves a PENDING request that only the operator decides, on the loopback listener
(ADR-0033; the steward route is the one remote exception, and it grants through
``hivemind.entrance.enrol.grants.steward_grant``). ``approve`` binds, in one step, everything the
device will be held to: its name, its ``CapabilitySet`` (never wider than the ``device`` role's
ceiling; the role's ``proposed`` set when the operator names none), its daily spend cap, its
expiry, and whether it is interactive (a person types the password at it: always for a passkey,
for a program key only when the operator says so). ``deny`` refuses the request with the
operator's reason. Each is one edge of the state machine, recorded as its ``guard.entrance_*``
event with the change and pushed to every other device. ``ApprovalRequest`` is what an approval
names; it crosses from ``hive entrance approve`` or the Observation Hive, so it is a validated
model.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Called by
    ``hive entrance approve`` and ``deny`` and their loopback-only routes (later steps). Calls
    into ``hivemind.entrance.enrol.grants`` (what may be granted) and
    ``hivemind.entrance.enrol.record`` (the edge with its event).

Key invariants:
    - An approval never grants beyond the device ceiling; one that names a capability beyond it
      is refused before anything is written, naming the offender.
    - An approval is decided by a person: its actor is ``"human"`` or a device id, never
      ``"system"``.
    - Only a PENDING device is approved or denied; a decision made from a stale read fails
      (``DeviceStatusConflictError``) instead of overwriting another.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for approval.
    - hivemind.entrance.enrol.grants for the ceiling and steward rules.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from hivemind.entrance.auth.keys import KeyKind
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.enrol.grants import approval_grant
from hivemind.entrance.enrol.models import (
    MAX_CAPABILITIES,
    MAX_DEVICE_NAME_CHARS,
    CapabilityText,
    DisplayText,
    EnrolledDevice,
)
from hivemind.entrance.enrol.record import (
    OPERATOR_ACTOR,
    Transition,
    apply_transition,
    iso,
    notify,
    reason_text,
)
from hivemind.entrance.enrol.state import APPROVED_TRAIL_KIND, DeviceStatus
from hivemind.entrance.errors import ConsoleProtectedError, InvalidApprovalError
from waggle.errors import InvalidIdError
from waggle.ids import DeviceId, IdKind, parse_id
from waggle.messages.base import UtcDatetime

__all__ = ["ApprovalRequest", "GrantChange", "approve", "deny", "regrant"]


class ApprovalRequest(BaseModel):
    """What the operator binds when approving a pending device (ADR-0033).

    Built by ``hive entrance approve`` or the Observation Hive on loopback from what the operator
    chose; every string in it is shown on approval surfaces again, so it is display text.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: DisplayText = Field(
        min_length=1,
        max_length=MAX_DEVICE_NAME_CHARS,
        description="The device's name from now on, e.g. 'Justin's phone'.",
    )
    capabilities: tuple[CapabilityText, ...] | None = Field(
        default=None,
        max_length=MAX_CAPABILITIES,
        description="What it may do, as capability strings inside the device ceiling; None "
        "grants the device role's proposed set.",
    )
    spend_cap_usd_per_day: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="The most it may spend per day, in USD; a hard limit step-up never lifts.",
    )
    expires_at: UtcDatetime | None = Field(
        default=None, description="When the approval lapses; None: it does not."
    )
    interactive: bool = Field(
        default=False,
        description="For an Ed25519 program key: a person types the password at it, so it may "
        "step up. Ignored for a passkey, which always is.",
    )
    actor: str = Field(
        description="Who approves: the approving device's id (the console, or a steward), or "
        "'human' for the operator at the Hive Stand's terminal."
    )

    @field_validator("actor")
    @classmethod
    def _actor_is_a_person_or_device(cls, value: str) -> str:
        """Accept ``"human"`` or a device id: an approval is never the system's own decision."""
        return _person_or_device(value)


async def approve(
    deps: EnrolmentDeps, device_id: DeviceId, request: ApprovalRequest
) -> EnrolledDevice:
    """Approve a PENDING device, binding its name, capabilities, spend cap, expiry and mode.

    Args:
        deps: The enrolment dependencies.
        device_id: The PENDING device.
        request: What the operator binds.

    Returns:
        The device as stored, APPROVED.

    Raises:
        DeviceNotFoundError: No such device.
        InvalidCapabilityError: A named string is not a capability.
        CapabilityCeilingError: A named capability is beyond the device ceiling.
        InvalidApprovalError: The expiry is not in the future.
        DeviceStatusConflictError: The device is not (or no longer) PENDING.
    """
    # Latency: one local primary-key read, for the key kind and the fingerprint the event shows.
    device = await deps.records.store.get_device(device_id)
    granted = approval_grant(deps.rules.policy, request.capabilities)
    now = deps.records.clock.now()
    _check_expiry(device_id, request, now)
    # A passkey ceremony always had a person there (user verification); a program key only
    # counts as interactive when the operator says a person types the password at it.
    interactive = device.key_kind is KeyKind.PASSKEY or request.interactive
    payload: dict[str, JsonValue] = {
        "fingerprint": device.fingerprint,
        "capability_count": len(granted),
        "spend_cap_usd_per_day": request.spend_cap_usd_per_day,
        "expires_at": iso(request.expires_at),
        "interactive": interactive,
    }
    edge = (DeviceStatus.PENDING, DeviceStatus.APPROVED)
    transition = Transition(device_id, *edge, request.actor, payload)
    return await apply_transition(
        deps,
        transition,
        name=request.name,
        capabilities=granted.as_strings(),
        spend_cap_usd_per_day=request.spend_cap_usd_per_day,
        expires_at=request.expires_at,
        interactive=interactive,
        approved_at=now,
    )


async def deny(deps: EnrolmentDeps, device_id: DeviceId, actor: str, reason: str) -> EnrolledDevice:
    """Refuse a PENDING device; it enrols again only with a new invite.

    Args:
        deps: The enrolment dependencies.
        device_id: The PENDING device.
        actor: Who refused it: the console's device id, or ``"human"`` at the Hive Stand.
        reason: Why, as the operator put it; recorded on the trail.

    Returns:
        The device as stored, DENIED.

    Raises:
        pydantic.ValidationError: ``reason`` is empty, too long or carries control characters,
            or ``actor`` is not an actor; nothing was written.
        DeviceNotFoundError: No such device.
        DeviceStatusConflictError: The device is not (or no longer) PENDING.
    """
    payload: dict[str, JsonValue] = {"reason": reason_text(reason)}
    transition = Transition(device_id, DeviceStatus.PENDING, DeviceStatus.DENIED, actor, payload)
    return await apply_transition(deps, transition)


def _check_expiry(device_id: DeviceId, request: ApprovalRequest, now: datetime) -> None:
    """Refuse an approval whose expiry is not in the future: it would lapse as it is granted."""
    if request.expires_at is not None and request.expires_at <= now:
        raise InvalidApprovalError(
            f"An approval of device {device_id} cannot expire at {request.expires_at.isoformat()}, "
            "which is not in the future."
        )


class GrantChange(BaseModel):
    """What the operator re-grants an approved device: its whole new set, and maybe a new cap.

    Built by the loopback capability route (after step-up) from what the operator chose; a
    re-grant is recorded as a fresh ``guard.entrance_approved`` of the new set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    capabilities: tuple[CapabilityText, ...] = Field(
        max_length=MAX_CAPABILITIES,
        description="The device's whole new capability set, inside the device ceiling.",
    )
    spend_cap_usd_per_day: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description="A new daily spend cap in USD; None keeps the current one.",
    )
    actor: str = Field(description="Who re-grants: the approving device's id, or 'human'.")

    @field_validator("actor")
    @classmethod
    def _actor_is_a_person_or_device(cls, value: str) -> str:
        """Accept ``"human"`` or a device id, as an approval does."""
        return _person_or_device(value)


async def regrant(deps: EnrolmentDeps, device_id: DeviceId, change: GrantChange) -> EnrolledDevice:
    """Replace an approved device's capability set (and cap), within the device ceiling.

    Args:
        deps: The enrolment dependencies.
        device_id: The APPROVED device.
        change: The new set, the new cap, and who decided.

    Returns:
        The device as stored, with its new grant.

    Raises:
        DeviceNotFoundError: No such device.
        ConsoleProtectedError: The device is the Hive Stand console, whose set is fixed.
        InvalidCapabilityError: A named string is not a capability.
        CapabilityCeilingError: A named capability is beyond the device ceiling.
        DeviceStatusConflictError: The device is not APPROVED.
    """
    records = deps.records
    # Latency: one local primary-key read, for the console check and the event's fingerprint.
    device = await records.store.get_device(device_id)
    # The console's set is what administers the Hive Stand: narrowing it could lock the operator
    # out, and it is loopback-bound anyway.
    if device.loopback_bound:
        raise ConsoleProtectedError(device_id, "re-granted")
    granted = approval_grant(deps.rules.policy, change.capabilities)
    cap = change.spend_cap_usd_per_day
    payload: dict[str, JsonValue] = {
        "regrant": True,
        "fingerprint": device.fingerprint,
        "capability_count": len(granted),
        "spend_cap_usd_per_day": cap if cap is not None else device.spend_cap_usd_per_day,
    }
    event = records.identity.event(
        records.clock, APPROVED_TRAIL_KIND, device_id, payload, change.actor
    )
    # Latency: one local transaction writing the new grant and its event together.
    if cap is None:
        updated = await records.store.update_device_grant(
            device_id, event, capabilities=granted.as_strings()
        )
    else:
        updated = await records.store.update_device_grant(
            device_id, event, capabilities=granted.as_strings(), spend_cap_usd_per_day=cap
        )
    await notify(deps, device_id, event)
    return updated


def _person_or_device(value: str) -> str:
    """Accept ``"human"`` or a device id as a decision's actor; refuse anything else."""
    if value == OPERATOR_ACTOR:
        return value
    try:
        return parse_id(value, IdKind.DEVICE)
    except InvalidIdError as exc:
        raise ValueError("A decision's actor is 'human' or a device id.") from exc
