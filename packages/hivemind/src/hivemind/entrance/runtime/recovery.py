"""Carry out, on start, the confirmed goals a crash kept from being submitted.

A program's goal that needs a person's step-up is held as a pending confirmation (ADR-0033), and a
person confirming it does two things in turn: the confirmation is settled CONFIRMED (the held
action is handed back once, never twice), then its goal request is committed in the Queen's table
under the id minted when it was held. A crash between the two would lose the goal: the
confirmation can never be confirmed again, and nothing else remembers the goal. So before the
Entrance listens, ``submit_confirmed_goals`` looks at every CONFIRMED goal hold and commits the
ones whose goal request does not exist yet. The minted id makes it idempotent: a request already
committed is found and left alone, and a second start finds them all committed. A hold whose
device is no longer approved (revoked, locked, lapsed meanwhile) is not carried out: a revoked
device's work never runs.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.runtime``. Called by
    ``HiveEntrance`` while it settles what a restart left, before anything listens. Calls into the
    pending table, the Queen's goal-request table and ``submit_held_goal`` (the goals route's own
    commit, the one a live confirmation uses).

Key invariants:
    - A goal request is committed at most once per hold: its id was minted when it was held.
    - Only a device still APPROVED, and not lapsed, has its confirmed goal carried out.

See Also:
    - hivemind.entrance.routes.entrance.held for the live confirmation this completes.
    - hivemind.entrance.routes.goals for ``submit_held_goal``.
"""

from __future__ import annotations

from hivemind.common.logging import get_logger
from hivemind.entrance.auth.confirm import PendingConfirmation, PendingStatus
from hivemind.entrance.auth.step_up import ActionKind
from hivemind.entrance.enrol import DeviceStatus, EnrolledDevice
from hivemind.entrance.errors import DeviceNotFoundError
from hivemind.entrance.gate import EntranceServices
from hivemind.entrance.routes.goals import submit_held_goal
from hivemind.queen.intake import GoalRequestNotFoundError

log = get_logger(__name__)

__all__ = ["submit_confirmed_goals"]


async def submit_confirmed_goals(services: EntranceServices) -> tuple[str, ...]:
    """Commit every confirmed goal hold whose goal request a crash kept from being committed.

    Args:
        services: The Entrance's services: the pending table, the devices, the Queen's door.

    Returns:
        The goal requests committed now, oldest hold first.
    """
    pending = services.enrolment.records.store.pending
    committed: list[str] = []
    # Latency: one local read of the pending table, then one read per confirmed goal hold.
    for held in await pending.list_by_status(PendingStatus.CONFIRMED):
        request_id = _request_id(held)
        if request_id is None or await _exists(services, request_id):
            continue
        device = await _approved(services, held)
        if device is None:
            # Its device lost its approval meanwhile: a revoked device's work never runs.
            log.info("entrance.confirmed_goal_dropped", pending_id=held.id)
            continue
        committed.append(await submit_held_goal(services, device, held.payload))
        log.info("entrance.confirmed_goal_recovered", pending_id=held.id, request_id=request_id)
    return tuple(committed)


def _request_id(held: PendingConfirmation) -> str | None:
    """Return the goal request id a held goal was minted with; None for any other hold."""
    if held.action is not ActionKind.GOAL:
        return None
    minted = held.payload.get("id")
    return minted if isinstance(minted, str) else None


async def _exists(services: EntranceServices, request_id: str) -> bool:
    """Return whether the Queen's table already holds the request."""
    try:
        # Latency: one local primary-key read of the Queen's goal-request table.
        await services.hive.goal_requests.get(request_id)
    except GoalRequestNotFoundError:
        return False
    return True


async def _approved(services: EntranceServices, held: PendingConfirmation) -> EnrolledDevice | None:
    """Return the device that asked, when it is still APPROVED and its approval has not lapsed."""
    try:
        # Latency: one local primary-key read of the Entrance tables.
        device = await services.enrolment.records.store.get_device(held.device_id)
    except DeviceNotFoundError:
        return None
    lapsed = device.expires_at is not None and device.expires_at <= services.clock.now()
    return device if device.status is DeviceStatus.APPROVED and not lapsed else None
