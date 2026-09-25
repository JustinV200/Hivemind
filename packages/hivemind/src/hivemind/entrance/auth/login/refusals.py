"""Refuse a login or a step-up the way ADR-0041 says: charge the right party, record, lock.

Which party a failure counts against is the heart of ADR-0041's lockout rule. An **invalid device
proof** never reaches the password check and never counts against the device it names, because
device ids are not secret and anyone could otherwise lock the operator out: it is charged to the
requesting address's rate limit instead and recorded for the Guard Bee. A **valid proof with a
wrong password** is a failure of that device: its consecutive-failure count goes up (persisted),
and at ``lockout_attempts`` in a row the device is locked through the enrolment step's ``lock``
(reason ``lockout``), which records ``guard.entrance_locked``, ends its sessions and tells every
other device; the count starts again after that. Every refusal is recorded as
``guard.entrance_login_failed`` before the one generic error is handed back.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.auth.login``. Called by
    the login flow and by step-up. Calls into the session failures recorder, the Entrance tables
    (``logins``), the rate limiter and ``hivemind.entrance.enrol.standing.lock``.

Key invariants:
    - An invalid proof never touches the device's failure count or its status.
    - A lock goes through ``lock``; no status is written here.
    - The failure is on the trail before the lock, and the lock before the refusal returns.

See Also:
    - docs/adr/0041-landing-board-enrolment-two-factor-login-and-exposure.md, "Lockout, rate
      limits, travel lock".
"""

from __future__ import annotations

from pydantic import JsonValue

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.login.deps import AuthDeps
from hivemind.entrance.auth.session.failures import (
    Failure,
    FailureReason,
    FailureStep,
    record_failure,
)
from hivemind.entrance.auth.session.models import Arrival
from hivemind.entrance.enrol.standing import LockReason, lock
from hivemind.entrance.errors import AuthenticationFailedError, DeviceStatusConflictError
from waggle.ids import DeviceId

LOCKOUT_ACTOR = "system"  # A lockout is the Entrance's own decision, not a person's.

log = get_logger(__name__)

__all__ = ["LOCKOUT_ACTOR", "refuse", "refuse_password", "refuse_proof"]


async def refuse(deps: AuthDeps, failure: Failure) -> AuthenticationFailedError:
    """Record a refusal that counts against nobody (a device that may not log in, say).

    Args:
        deps: The login dependencies.
        failure: What was refused, where, and from where.

    Returns:
        The generic error to raise.
    """
    return await record_failure(deps.records, failure)


async def refuse_proof(
    deps: AuthDeps, arrival: Arrival, step: FailureStep, device_id: DeviceId | None
) -> AuthenticationFailedError:
    """Refuse an invalid device proof: charge its address, record it, never touch the device.

    Args:
        deps: The login dependencies.
        arrival: Where the proof came from; its address pays.
        step: Login or step-up.
        device_id: The device the proof named, when its record exists (the event's subject).

    Returns:
        The generic error to raise.
    """
    deps.guards.limiter.charge_address(arrival.address)
    failure = Failure(FailureReason.PROOF, step, arrival, device_id)
    return await record_failure(deps.records, failure)


async def refuse_password(
    deps: AuthDeps, device_id: DeviceId, arrival: Arrival, step: FailureStep
) -> AuthenticationFailedError:
    """Refuse a wrong password after a valid proof: count it against the device, lock at the limit.

    Args:
        deps: The login dependencies.
        device_id: The device whose proof held.
        arrival: Where the attempt came from.
        step: Login or step-up.

    Returns:
        The generic error to raise; the device is LOCKED by then if this failure reached the
        limit.
    """
    records = deps.records
    # Latency: one local upsert that increments in SQL and returns the new count.
    count = await records.store.logins.count_failure(device_id, records.clock.now())
    details: dict[str, JsonValue] = {
        "consecutive_failures": count,
        "lockout_attempts": deps.guards.lockout_attempts,
    }
    error = await record_failure(
        records, Failure(FailureReason.PASSWORD, step, arrival, device_id, details)
    )
    if count >= deps.guards.lockout_attempts:
        await _lock_out(deps, device_id)
    return error


async def _lock_out(deps: AuthDeps, device_id: DeviceId) -> None:
    """Lock the device through the enrolment step, then start its count again."""
    try:
        # Latency: one local transaction, then offboarding (its sessions end) and a notice.
        await lock(deps.enrolment, device_id, LOCKOUT_ACTOR, LockReason.LOCKOUT)
    except DeviceStatusConflictError:
        # Locked, revoked or expired meanwhile: that decision stands, and it already cut it off.
        log.debug("entrance.lockout_superseded", device_id=device_id)
    else:
        log.info("entrance.locked_out", device_id=device_id)
    # Latency: one local delete.
    await deps.records.store.logins.clear_failures(device_id)
