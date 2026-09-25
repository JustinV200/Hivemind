"""Define PendingTable: where held requests wait for a person to confirm them.

A device no person types at cannot step up, so what it asks for that needs step-up is held as a
pending confirmation until a person confirms it from an interactive device (ADR-0041).
``PendingTable`` keeps them (codingrules 8.1), implemented by ``SqlitePendingTable`` and
``MemoryPendingTable``. The pure functions here are the rules both apply inside their own atomic
step: ``check_new_pending`` (a confirmation is put PENDING, with its ``guard.entrance_held``
event) and ``settle_pending`` (the single place its status moves: an edge of
``hivemind.entrance.auth.confirm.state``, from the status the settler expected, so of two people
confirming at once the second fails instead of carrying it out twice, with the edge's event).
Every change is written together with its trail event, or neither is (codingrules Appendix C).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.pending``. Used by
    ``hivemind.entrance.auth.confirm``; reached through ``EntranceStore.pending``. Calls into that
    package's models and state machine, and ``hivemind.common.errors``.

Key invariants:
    - A confirmation is put PENDING and settled at most once, each with its own trail event about
      the device that asked, naming the confirmation (``check_pending_event``).
    - ``settle`` names the status it expects; a confirmation that moved meanwhile raises
      ``PendingStatusConflictError`` and is left as it is.

See Also:
    - hivemind.entrance.auth.confirm.state for the transition table.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.auth.confirm.models import PendingConfirmation, PendingId, Settlement
from hivemind.entrance.auth.confirm.state import (
    HELD_KIND,
    PendingStatus,
    assert_pending_transition,
    settled_trail_kind,
)
from hivemind.entrance.errors import PendingStatusConflictError
from hivemind.pheromone import GuardEvent

__all__ = ["PendingTable", "check_new_pending", "check_pending_event", "settle_pending"]


class PendingTable(Protocol):
    """Keep held requests, and settle each one once."""

    async def put(self, pending: PendingConfirmation, event: GuardEvent) -> None:
        """Record a newly held request with its ``guard.entrance_held`` event, atomically.

        Args:
            pending: The confirmation, PENDING.
            event: Its ``guard.entrance_held`` event, about the device that asked.

        Raises:
            InvariantViolationError: It is not PENDING, its id is taken, its device is unknown,
                or the event is not its held event.
            DuplicateEventError: The event's id is already on the trail.
        """
        ...

    async def get(self, pending_id: PendingId) -> PendingConfirmation:
        """Return one confirmation, settled or not.

        Args:
            pending_id: Its id.

        Returns:
            The stored confirmation.

        Raises:
            PendingNotFoundError: No such confirmation.
        """
        ...

    async def settle(
        self,
        pending_id: PendingId,
        expected: PendingStatus,
        settlement: Settlement,
        event: GuardEvent,
    ) -> PendingConfirmation:
        """Move a confirmation along one edge with its event, from the status the settler expected.

        Args:
            pending_id: The confirmation.
            expected: The status the settler decided from (PENDING).
            settlement: The new status, when, and who confirmed it.
            event: The edge's event (``settled_trail_kind``), about the device that asked.

        Returns:
            The confirmation as stored after the change.

        Raises:
            PendingNotFoundError: No such confirmation.
            InvalidPendingTransitionError: ``expected`` to the new status is not an edge.
            PendingStatusConflictError: It is no longer in ``expected``.
            InvariantViolationError: The event is not this edge's event about this confirmation.
            pydantic.ValidationError: The settlement breaks a model rule (a confirmation without
                its confirming device).
        """
        ...

    async def list_by_status(
        self, status: PendingStatus | None = None
    ) -> tuple[PendingConfirmation, ...]:
        """Return every confirmation, or every one in ``status``, oldest first.

        Args:
            status: Only confirmations in this status; None for all of them.

        Returns:
            The confirmations ordered by ``created_at``, then id.
        """
        ...


def check_new_pending(pending: PendingConfirmation, event: GuardEvent) -> None:
    """Refuse to record a confirmation that is not waiting, or without its held event.

    Args:
        pending: The confirmation ``put`` was given.
        event: The event ``put`` was given.

    Raises:
        InvariantViolationError: It is not PENDING, or ``event`` is not its held event.
    """
    if pending.status is not PendingStatus.PENDING:
        raise InvariantViolationError(
            f"Pending confirmation {pending.id} is recorded PENDING, not {pending.status.name}."
        )
    check_pending_event(pending, HELD_KIND, event)


def check_pending_event(pending: PendingConfirmation, kind: str, event: GuardEvent) -> None:
    """Require ``event`` to be the ``kind`` event about ``pending``'s device, naming ``pending``.

    Args:
        pending: The confirmation the change is about.
        kind: The ``guard.entrance_*`` kind that change is recorded as.
        event: The event about to be written with it.

    Raises:
        InvariantViolationError: ``event`` is of another kind, about another device, or names
            another confirmation.
    """
    names = event.payload.get("pending_id")
    if event.kind != kind or event.subject_id != pending.device_id or names != pending.id:
        raise InvariantViolationError(
            f"Event {event.id} is {event.kind} about {event.subject_id}; a change of pending "
            f"confirmation {pending.id} must record {kind} about device {pending.device_id}."
        )


def settle_pending(
    current: PendingConfirmation,
    expected: PendingStatus,
    settlement: Settlement,
    event: GuardEvent,
) -> PendingConfirmation:
    """Apply one settlement to ``current``: the single place a confirmation's status moves.

    Args:
        current: The confirmation as stored right now.
        expected: The status the settler decided from.
        settlement: The new status, when, and who confirmed it.
        event: The edge's event, written with the result.

    Returns:
        A new, fully re-validated confirmation.

    Raises:
        InvalidPendingTransitionError: ``expected`` to the new status is not an edge.
        PendingStatusConflictError: ``current`` is not in ``expected``.
        InvariantViolationError: ``event`` is not the edge's event about ``current``.
        pydantic.ValidationError: The result breaks a model rule.
    """
    # The edge first: asking for an impossible move is a bug whatever the stored status is.
    assert_pending_transition(expected, settlement.new)
    check_pending_event(current, settled_trail_kind(settlement.new), event)
    if current.status is not expected:
        raise PendingStatusConflictError(current.id, expected, current.status)
    # model_validate (not model_copy) so every field and cross-field rule runs on the result.
    fields: dict[str, object] = dict(current)
    fields.update(
        status=settlement.new, settled_at=settlement.at, confirmed_by=settlement.confirmed_by
    )
    return PendingConfirmation.model_validate(fields)
