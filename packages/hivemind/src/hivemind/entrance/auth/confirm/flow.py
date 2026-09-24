"""Hold a request a device cannot step up for, and let a person confirm it, once.

A device no person types at (a program key) cannot step up, so a request of its that needs step-up
is not refused outright and not let through on its own two factors either (ADR-0033): ``hold`` keeps
it as a pending confirmation (the action's kind, a bounded payload the caller needs later, an
expiry), records ``guard.entrance_held`` with it, tells every other approved device (the human
must be reached) and returns its id, which the route answers ``403 step_up_required`` with.
``confirm`` lets a person carry it through: only from an interactive device whose session is
stepped up right now, only while it is PENDING and unexpired, and only while the device that asked
is still approved; it settles the confirmation CONFIRMED with ``guard.entrance_confirmed`` (once: of
two people confirming, the second fails) and hands back the ``HeldAction`` for the caller to carry
out. ``cancel`` declines one; ``expire_pending`` is the sweep; both end it with
``guard.entrance_hold_ended``. A break-glass action (Absconding, Sting Cut, Supersedure) is never
held: it needs a person at an interactive device from the start. A program's goal above its spend
cap is therefore refused pending a human, never by its own two factors.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.confirm``. Called by
    the routes that guard sensitive actions and by the confirmation routes. Calls into the Entrance
    tables (``pending``, the devices) through ``EnrolmentRecords``, and the security notifier
    through ``EnrolmentDeps``.

Key invariants:
    - Only a non-interactive device's request is held, and never a break-glass one.
    - Only an interactive, stepped-up session confirms, and a held action is handed back once.
    - An expired or orphaned confirmation is settled (EXPIRED, CANCELLED) when it is found.
    - Every change is written with its ``guard.entrance_*`` event, which names the confirmation,
      the device and the action, never the held request's content.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md, "Step-up needs a
      human".
    - hivemind.entrance.auth.confirm.state for the state machine and its trail kinds.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.confirm.models import (
    HeldAction,
    PendingConfirmation,
    PendingId,
    Settlement,
    new_pending_id,
)
from hivemind.entrance.auth.confirm.state import HELD_KIND, PendingStatus, settled_trail_kind
from hivemind.entrance.auth.session.models import AuthenticatedSession
from hivemind.entrance.auth.step_up.rules import BREAK_GLASS_ACTIONS, ActionKind
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.record import iso, notify
from hivemind.entrance.enrol.state import DeviceStatus
from hivemind.entrance.errors import (
    ConfirmationRefusedError,
    DeviceNotFoundError,
    PendingStatusConflictError,
)
from hivemind.pheromone import GuardEvent

if TYPE_CHECKING:
    # Type-only: the enrolment bundle refers to the store protocol, which imports these models.
    from hivemind.entrance.enrol.deps import EnrolmentDeps, EnrolmentRecords

DEFAULT_CONFIRMATION_TTL = timedelta(hours=1)  # Long enough to reach a person; short enough that
# a request is never carried out long after the device stopped waiting for it.
ENDED_EXPIRED = "expired"  # guard.entrance_hold_ended's "ended": nobody confirmed it in time.
ENDED_CANCELLED = "cancelled"  # ... a person declined it, or its device lost its approval.

log = get_logger(__name__)

__all__ = [
    "DEFAULT_CONFIRMATION_TTL",
    "ENDED_CANCELLED",
    "ENDED_EXPIRED",
    "cancel",
    "confirm",
    "expire_pending",
    "hold",
]


async def hold(
    deps: EnrolmentDeps,
    device: EnrolledDevice,
    action: ActionKind,
    payload: Mapping[str, JsonValue],
    ttl: timedelta = DEFAULT_CONFIRMATION_TTL,
) -> PendingId:
    """Hold a non-interactive device's request until a person confirms it, and tell the others.

    Args:
        deps: The Entrance tables, the clock, the identity and the security notifier.
        device: The device asking, APPROVED and not interactive.
        action: What it asks for.
        payload: What the caller needs to carry it out later (at most 64 KiB of JSON); it stays in
            the pending table and never reaches the trail or a push.
        ttl: How long it may wait for a person; must be positive.

    Returns:
        The pending confirmation's id, for the ``403 step_up_required`` answer and the push.

    Raises:
        ConfirmationRefusedError: The device is interactive (it steps up itself), not APPROVED,
            or the action is break-glass (only an interactive device may ask for one).
        pydantic.ValidationError: The payload is too large.
    """
    if device.interactive:
        raise ConfirmationRefusedError(
            f"Device {device.id} is interactive: it steps up itself instead of waiting."
        )
    if device.status is not DeviceStatus.APPROVED:
        raise ConfirmationRefusedError(f"Device {device.id} is {device.status.name}, not APPROVED.")
    # ADR-0033: break-glass only ever comes from an interactive device, so it is never held.
    if action in BREAK_GLASS_ACTIONS or ttl <= timedelta(0):
        raise ConfirmationRefusedError(f"{action.value} cannot be held for device {device.id}.")
    records = deps.records
    now = records.clock.now()
    pending = PendingConfirmation(
        id=new_pending_id(records.clock),
        device_id=device.id,
        action=action,
        payload=dict(payload),
        status=PendingStatus.PENDING,
        created_at=now,
        expires_at=now + ttl,
    )
    extra: dict[str, JsonValue] = {"expires_at": iso(pending.expires_at)}
    event = _event(records, pending, HELD_KIND, device.id, extra)
    # Latency: one local transaction writing the held request and its event together.
    await records.store.pending.put(pending, event)
    # Every other approved device hears that a request waits for a person (ADR-0033).
    await notify(deps, device.id, event)
    log.info("entrance.held", pending_id=pending.id, device_id=device.id, action=action.value)
    return pending.id


async def confirm(
    records: EnrolmentRecords, pending_id: PendingId, confirming: AuthenticatedSession
) -> HeldAction:
    """Confirm a held request from an interactive, stepped-up session; hand back its action once.

    Args:
        records: The Entrance tables and the clock.
        pending_id: The confirmation.
        confirming: The session confirming it.

    Returns:
        The held action, for the caller to carry out exactly once.

    Raises:
        ConfirmationRefusedError: The session is not interactive and stepped up, or the
            confirmation is settled, expired, or its device is no longer approved (those last
            two are settled EXPIRED or CANCELLED on the way).
        PendingNotFoundError: No such confirmation.
    """
    if not (confirming.interactive and confirming.stepped_up):
        raise ConfirmationRefusedError(
            "Only an interactive device that has just stepped up confirms a held request."
        )
    # Latency: one local read.
    pending = await records.store.pending.get(pending_id)
    now = records.clock.now()
    await _require_live(records, pending, now)
    by = confirming.device.id
    settlement = Settlement(PendingStatus.CONFIRMED, now, confirmed_by=by)
    event = _event(records, pending, settled_trail_kind(PendingStatus.CONFIRMED), by, {"by": by})
    try:
        # Latency: one local transaction; of two people confirming, exactly one gets here.
        settled = await records.store.pending.settle(
            pending_id, PendingStatus.PENDING, settlement, event
        )
    except PendingStatusConflictError:
        raise ConfirmationRefusedError(f"Pending confirmation {pending_id} was settled.") from None
    log.info("entrance.confirmed", pending_id=pending_id, by=by)
    return HeldAction(
        pending_id=settled.id,
        device_id=settled.device_id,
        action=settled.action,
        payload=dict(settled.payload),
        confirmed_by=by,
    )


async def cancel(
    records: EnrolmentRecords, pending_id: PendingId, session: AuthenticatedSession
) -> PendingConfirmation:
    """Decline a held request from an interactive session (declining needs no step-up).

    Args:
        records: The Entrance tables and the clock.
        pending_id: The confirmation.
        session: The session declining it.

    Returns:
        The confirmation, CANCELLED.

    Raises:
        ConfirmationRefusedError: The session is not interactive.
        PendingNotFoundError: No such confirmation.
        PendingStatusConflictError: It was settled already.
    """
    if not session.interactive:
        raise ConfirmationRefusedError("Only an interactive device declines a held request.")
    # Latency: one local read; the event names the device that asked, which never changes.
    pending = await records.store.pending.get(pending_id)
    settlement = Settlement(PendingStatus.CANCELLED, records.clock.now())
    extra: dict[str, JsonValue] = {"ended": ENDED_CANCELLED}
    kind = settled_trail_kind(PendingStatus.CANCELLED)
    event = _event(records, pending, kind, session.device.id, extra)
    # Latency: one local transaction re-reading and settling the row with its event.
    return await records.store.pending.settle(pending_id, PendingStatus.PENDING, settlement, event)


async def expire_pending(records: EnrolmentRecords) -> int:
    """Settle every held request past its expiry as EXPIRED.

    Args:
        records: The Entrance tables and the clock.

    Returns:
        How many this call expired.
    """
    now = records.clock.now()
    expired = 0
    # A snapshot of what is held; one confirmed or declined meanwhile is left to that decision.
    for pending in await records.store.pending.list_by_status(PendingStatus.PENDING):
        if pending.expires_at <= now and await _settle_quietly(records, pending, now, expire=True):
            expired += 1
    return expired


async def _require_live(
    records: EnrolmentRecords, pending: PendingConfirmation, now: datetime
) -> None:
    """Refuse a settled, expired or orphaned confirmation, settling the last two on the way."""
    if pending.status is not PendingStatus.PENDING:
        raise ConfirmationRefusedError(
            f"Pending confirmation {pending.id} is {pending.status.name}."
        )
    if pending.expires_at <= now:
        await _settle_quietly(records, pending, now, expire=True)
        raise ConfirmationRefusedError(f"Pending confirmation {pending.id} has expired.")
    # The device that asked must still be approved: a revoked program's goal is never run.
    if not await _still_approved(records, pending, now):
        await _settle_quietly(records, pending, now, expire=False)
        raise ConfirmationRefusedError(
            f"Device {pending.device_id} is no longer approved; its held request is cancelled."
        )


async def _still_approved(
    records: EnrolmentRecords, pending: PendingConfirmation, now: datetime
) -> bool:
    """Return whether the device that asked is APPROVED and its approval has not lapsed."""
    try:
        # Latency: one local primary-key read.
        device = await records.store.get_device(pending.device_id)
    except DeviceNotFoundError:
        return False
    lapsed = device.expires_at is not None and device.expires_at <= now
    return device.status is DeviceStatus.APPROVED and not lapsed


async def _settle_quietly(
    records: EnrolmentRecords, pending: PendingConfirmation, now: datetime, *, expire: bool
) -> bool:
    """Settle ``pending`` EXPIRED (or CANCELLED); False when another settlement won the race."""
    status = PendingStatus.EXPIRED if expire else PendingStatus.CANCELLED
    extra: dict[str, JsonValue] = {"ended": ENDED_EXPIRED if expire else ENDED_CANCELLED}
    event = _event(records, pending, settled_trail_kind(status), None, extra)
    try:
        # Latency: one local transaction re-reading and settling the row with its event.
        await records.store.pending.settle(
            pending.id, PendingStatus.PENDING, Settlement(status, now), event
        )
    except PendingStatusConflictError:
        log.debug("entrance.settlement_superseded", pending_id=pending.id)
        return False
    return True


def _event(
    records: EnrolmentRecords,
    pending: PendingConfirmation,
    kind: str,
    actor: str | None,
    extra: Mapping[str, JsonValue],
) -> GuardEvent:
    """Build a change's event: about the device that asked, naming the confirmation and action."""
    payload: dict[str, JsonValue] = {"pending_id": pending.id, "action": pending.action.value}
    payload.update(extra)
    return records.identity.event(records.clock, kind, pending.device_id, payload, actor)
