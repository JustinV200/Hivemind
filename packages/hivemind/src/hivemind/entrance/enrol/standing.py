"""Change an admitted device's standing: revoke it, lock and unlock it, and expire what lapsed.

Once approved, a device's standing changes four ways (ADR-0033, codingrules Appendix C).
``revoke`` withdraws it for good on the loopback listener: every goal request it submitted that is
not planned yet is refused (so its work never starts), the goals it submitted that are still open
are listed and, when the operator asks, cancelled in the same step (placed work is stopped on its
Warden), and the revocation event names what was refused, cancelled and left running. ``lock``
narrows it (a lockout after failed logins, a burst of capability denials, or another device
locking a lost one) and ``unlock`` reopens it, on loopback only. ``expire_due`` is the sweep the
Entrance runs on a timer: every invite, request or approval whose ``expires_at`` has passed moves
to EXPIRED. Leaving APPROVED always ends the device's
sessions and push subscriptions (``hivemind.entrance.enrol.record``). The Hive Stand's own console
can be locked and unlocked like any other device, but nothing here revokes or expires it: losing
it means ``hive entrance operator password --reset``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.enrol``. Called by
    ``hive entrance revoke`` and ``unlock`` and their loopback-only routes, the lockout and
    remote-lock paths of the login routes (roadmap 10.5e) and the Entrance's expiry timer (later
    steps). Calls into ``hivemind.entrance.enrol.record`` and the enrolment seams (the goal ledger).

Key invariants:
    - The console is never revoked or expired here; ``revoke`` refuses it with
      ``ConsoleProtectedError`` and ``expire_due`` never selects it.
    - A revocation's event names every request refused, every goal cancelled and every goal left
      running (up to ``MAX_IDS_ON_TRAIL`` each, with the full counts).
    - ``expire_due`` never overrides a concurrent decision: a device that moved since the sweep
      read it is left to that decision.

See Also:
    - docs/adr/0033-landing-board-enrolment-two-factor-login-and-exposure.md for each edge.
    - hivemind.entrance.enrol.deps.goals for the goal ledger revocation uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.enrol.deps import EnrolmentDeps
from hivemind.entrance.enrol.models import EnrolledDevice
from hivemind.entrance.enrol.record import Transition, apply_transition, iso, named_ids
from hivemind.entrance.enrol.state import DeviceStatus
from hivemind.entrance.errors import ConsoleProtectedError, DeviceStatusConflictError
from waggle.ids import DeviceId, TaskId

OPERATOR_REVOKED = "operator"  # The reason a revocation by the operator records.
# The statuses whose expires_at the sweep enforces: an invite, a request and an approval (held
# while locked too) each lapse; the terminal statuses have nothing left to lapse.
EXPIRING_STATUSES = (
    DeviceStatus.INVITED,
    DeviceStatus.PENDING,
    DeviceStatus.APPROVED,
    DeviceStatus.LOCKED,
)
# The statuses revoke acts on; an INVITED device is withdrawn with cancel_invite, a PENDING one
# refused with deny.
_REVOCABLE = frozenset({DeviceStatus.APPROVED, DeviceStatus.LOCKED})

log = get_logger(__name__)

__all__ = [
    "EXPIRING_STATUSES",
    "OPERATOR_REVOKED",
    "LockReason",
    "Revocation",
    "expire_due",
    "lock",
    "revoke",
    "unlock",
]


class LockReason(Enum):
    """Why a device was locked; recorded on the trail with the lock."""

    LOCKOUT = "lockout"  # lockout_attempts valid-proof login failures in a row.
    DENIAL_BURST = "denial_burst"  # lockout_denials capability denials inside the window.
    REMOTE_LOCK = "remote_lock"  # Another interactive device locked it after step-up (lost).


@dataclass(frozen=True, slots=True)
class Revocation:
    """What a revocation did: the revoked device, its refused requests, its open goals.

    Attributes:
        device: The device as stored, REVOKED.
        goals_cancelled: The open goals cancelled in the same step.
        goals_left_running: The open goals still running: all of them when cancelling was not
            asked for, or those whose work could not be stopped (its Warden unreachable).
        requests_refused: The goal requests it submitted that were not planned yet, refused
            in the same step whatever was asked, so they never run.
    """

    device: EnrolledDevice
    goals_cancelled: tuple[TaskId, ...]
    goals_left_running: tuple[TaskId, ...]
    requests_refused: tuple[str, ...] = ()


async def revoke(
    deps: EnrolmentDeps, device_id: DeviceId, actor: str, cancel_goals: bool
) -> Revocation:
    """Revoke an approved or locked device, cancelling its open goals when asked.

    Args:
        deps: The enrolment dependencies.
        device_id: The device to revoke.
        actor: Who revoked it: the console's device id, or ``"human"`` at the Hive Stand.
        cancel_goals: Cancel the goals it submitted that are still open (``--cancel-goals``).

    Returns:
        The revoked device and what became of its open goals.

    Raises:
        DeviceNotFoundError: No such device.
        ConsoleProtectedError: It is the Hive Stand console.
        DeviceStatusConflictError: It is neither APPROVED nor LOCKED (or moved meanwhile).
    """
    # Latency: one local primary-key read.
    device = await deps.records.store.get_device(device_id)
    # The console is never revoked here: losing it means resetting the operator password.
    if device.loopback_bound:
        raise ConsoleProtectedError(device.id, "revoked")
    # An INVITED device is withdrawn with cancel_invite, a PENDING one refused with deny.
    if device.status not in _REVOCABLE:
        raise DeviceStatusConflictError(device.id, DeviceStatus.APPROVED, device.status)
    # What it asked for and nobody planned yet never runs, whatever the operator asked.
    # Latency: the goal ledger is the Queen's own table in the same process; milliseconds.
    refused = await deps.seams.goals.refuse_requests(device.id, _revoked_reason(device.id))
    # Goals next, so the event can name exactly what was cancelled and what is left running.
    open_goals = await deps.seams.goals.open_goals(device.id)
    cancelled = await _cancelled(deps, device.id, open_goals, cancel_goals)
    left_running = tuple(goal for goal in open_goals if goal not in cancelled)
    payload: dict[str, JsonValue] = {
        "reason": OPERATOR_REVOKED,
        "requests_refused": named_ids(refused),
        "requests_refused_count": len(refused),
        "goals_cancelled": named_ids(cancelled),
        "goals_cancelled_count": len(cancelled),
        "goals_left_running": named_ids(left_running),
        "goals_left_running_count": len(left_running),
    }
    transition = Transition(device.id, device.status, DeviceStatus.REVOKED, actor, payload)
    revoked = await apply_transition(deps, transition)
    return Revocation(revoked, cancelled, left_running, refused)


async def lock(
    deps: EnrolmentDeps, device_id: DeviceId, actor: str, reason: LockReason
) -> EnrolledDevice:
    """Lock an approved device: its sessions end until a loopback unlock.

    Args:
        deps: The enrolment dependencies.
        device_id: The APPROVED device (the console included).
        actor: Who locked it: ``"system"`` for a lockout or a denial burst, or the locking
            device's id.
        reason: Why.

    Returns:
        The device as stored, LOCKED.

    Raises:
        DeviceNotFoundError: No such device.
        DeviceStatusConflictError: It is not (or no longer) APPROVED.
    """
    payload: dict[str, JsonValue] = {"reason": reason.value}
    transition = Transition(device_id, DeviceStatus.APPROVED, DeviceStatus.LOCKED, actor, payload)
    return await apply_transition(deps, transition)


async def unlock(deps: EnrolmentDeps, device_id: DeviceId, actor: str) -> EnrolledDevice:
    """Unlock a locked device on loopback; it may log in again (its sessions stay ended).

    Args:
        deps: The enrolment dependencies.
        device_id: The LOCKED device (the console included).
        actor: Who unlocked it: the console's device id, or ``"human"`` at the Hive Stand.

    Returns:
        The device as stored, APPROVED again.

    Raises:
        DeviceNotFoundError: No such device.
        DeviceStatusConflictError: It is not (or no longer) LOCKED.
    """
    transition = Transition(device_id, DeviceStatus.LOCKED, DeviceStatus.APPROVED, actor, {})
    return await apply_transition(deps, transition)


async def expire_due(deps: EnrolmentDeps) -> int:
    """Move every invite, request and approval past its ``expires_at`` to EXPIRED.

    Args:
        deps: The enrolment dependencies; events are recorded as its identity's actor.

    Returns:
        How many devices this call expired.
    """
    now = deps.records.clock.now()
    expired = 0
    # Status by status: each list is a snapshot, and a device that moves meanwhile is skipped.
    for status in EXPIRING_STATUSES:
        # Latency: one indexed local read per status; a Hive holds a handful of devices.
        for device in await deps.records.store.list_devices(status):
            if _is_due(device, now):
                expired += await _expire(deps, device)
    return expired


async def _cancelled(
    deps: EnrolmentDeps, device_id: DeviceId, open_goals: tuple[TaskId, ...], cancel: bool
) -> tuple[TaskId, ...]:
    """Cancel the open goals when asked; return the ones the ledger actually cancelled."""
    # Nothing asked for, or nothing to cancel: the ledger is not bothered.
    if not cancel or not open_goals:
        return ()
    reason = _revoked_reason(device_id)
    # Latency: local, like open_goals; each cancellation is the Queen's own trail event.
    done = await deps.seams.goals.cancel_goals(open_goals, reason)
    # Only goals that were open count; the ledger's answer is trusted no further than that.
    return tuple(goal for goal in open_goals if goal in done)


def _revoked_reason(device_id: DeviceId) -> str:
    """The phrase a revocation's refusals and cancellations record: ids only, never content."""
    return f"device {device_id} was revoked"


def _is_due(device: EnrolledDevice, now: datetime) -> bool:
    """Return whether ``device`` has lapsed; the console never does."""
    # The console is never expired here, and a record without an expiry never lapses.
    if device.loopback_bound or device.expires_at is None:
        return False
    return device.expires_at <= now


async def _expire(deps: EnrolmentDeps, device: EnrolledDevice) -> int:
    """Expire one lapsed device; return 1, or 0 when a concurrent decision moved it first."""
    payload: dict[str, JsonValue] = {
        "expired_from": device.status.value,
        "expires_at": iso(device.expires_at),
    }
    actor = deps.records.identity.actor
    transition = Transition(device.id, device.status, DeviceStatus.EXPIRED, actor, payload)
    try:
        await apply_transition(deps, transition)
    except DeviceStatusConflictError:
        # An approval, a denial or a revocation won the race; that decision stands.
        log.debug("entrance.expiry_superseded", device_id=device.id)
        return 0
    return 1
