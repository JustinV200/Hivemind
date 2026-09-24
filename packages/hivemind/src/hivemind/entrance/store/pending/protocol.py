"""Define PendingTable: where held requests wait for a person to confirm them.

A device no person types at cannot step up, so what it asks for that needs step-up is held as a
pending confirmation until a person confirms it from an interactive device (ADR-0033).
``PendingTable`` keeps them (codingrules 8.1), implemented by ``SqlitePendingTable`` and
``MemoryPendingTable``. The pure functions here are the rules both apply inside their own atomic
step: ``check_new_pending`` (a confirmation is put PENDING) and ``settle_pending`` (the single place
its status moves: an edge of ``hivemind.entrance.auth.confirm.state``, from the status the settler
expected, so of two people confirming at once the second fails instead of carrying it out twice).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.pending``. Used by
    ``hivemind.entrance.auth.confirm``; reached through ``EntranceStore.pending``. Calls into that
    package's models and state machine, and ``hivemind.common.errors``.

Key invariants:
    - A confirmation is put PENDING and settled at most once.
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
from hivemind.entrance.auth.confirm.state import PendingStatus, assert_pending_transition
from hivemind.entrance.errors import PendingStatusConflictError

__all__ = ["PendingTable", "check_new_pending", "settle_pending"]


class PendingTable(Protocol):
    """Keep held requests, and settle each one once."""

    async def put(self, pending: PendingConfirmation) -> None:
        """Record a newly held request.

        Args:
            pending: The confirmation, PENDING.

        Raises:
            InvariantViolationError: It is not PENDING, its id is taken, or its device is unknown.
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
        self, pending_id: PendingId, expected: PendingStatus, settlement: Settlement
    ) -> PendingConfirmation:
        """Move a confirmation along one edge, from the status the settler expected.

        Args:
            pending_id: The confirmation.
            expected: The status the settler decided from (PENDING).
            settlement: The new status, when, and who confirmed it.

        Returns:
            The confirmation as stored after the change.

        Raises:
            PendingNotFoundError: No such confirmation.
            InvalidPendingTransitionError: ``expected`` to the new status is not an edge.
            PendingStatusConflictError: It is no longer in ``expected``.
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


def check_new_pending(pending: PendingConfirmation) -> None:
    """Refuse to record a confirmation that is not waiting to be confirmed.

    Args:
        pending: The confirmation ``put`` was given.

    Raises:
        InvariantViolationError: It is not PENDING.
    """
    if pending.status is not PendingStatus.PENDING:
        raise InvariantViolationError(
            f"Pending confirmation {pending.id} is recorded PENDING, not {pending.status.name}."
        )


def settle_pending(
    current: PendingConfirmation, expected: PendingStatus, settlement: Settlement
) -> PendingConfirmation:
    """Apply one settlement to ``current``: the single place a confirmation's status moves.

    Args:
        current: The confirmation as stored right now.
        expected: The status the settler decided from.
        settlement: The new status, when, and who confirmed it.

    Returns:
        A new, fully re-validated confirmation.

    Raises:
        InvalidPendingTransitionError: ``expected`` to the new status is not an edge.
        PendingStatusConflictError: ``current`` is not in ``expected``.
        pydantic.ValidationError: The result breaks a model rule.
    """
    # The edge first: asking for an impossible move is a bug whatever the stored status is.
    assert_pending_transition(expected, settlement.new)
    if current.status is not expected:
        raise PendingStatusConflictError(current.id, expected, current.status)
    # model_validate (not model_copy) so every field and cross-field rule runs on the result.
    fields: dict[str, object] = dict(current)
    fields.update(
        status=settlement.new, settled_at=settlement.at, confirmed_by=settlement.confirmed_by
    )
    return PendingConfirmation.model_validate(fields)
