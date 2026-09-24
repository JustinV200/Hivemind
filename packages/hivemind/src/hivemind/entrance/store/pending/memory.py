"""Provide MemoryPendingTable: held requests in a dict, for tests and demos.

Codingrules 14.4 keeps fakes beside their protocol, honest and production quality. This one keeps
pending confirmations in memory under one lock and applies the same rules ``SqlitePendingTable``
applies (``hivemind.entrance.store.pending.protocol``), so the contract suite runs unchanged over
both.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.pending``. Held by
    ``MemoryEntranceStore``. Calls into the protocol's rules only.

Key invariants:
    - Every method holds the lock for its whole body, so a settlement is atomic.
    - Behaves exactly like SqlitePendingTable under the Entrance store contract suite.

See Also:
    - hivemind.entrance.store.pending.protocol for PendingTable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.auth.confirm.models import PendingConfirmation, PendingId, Settlement
from hivemind.entrance.auth.confirm.state import PendingStatus
from hivemind.entrance.errors import PendingNotFoundError
from hivemind.entrance.store.pending.protocol import check_new_pending, settle_pending
from waggle.ids import DeviceId

__all__ = ["MemoryPendingTable"]


class MemoryPendingTable:
    """Held requests by id, gone when the process exits."""

    def __init__(self, known_device: Callable[[DeviceId], bool] | None = None) -> None:
        """Start with nothing held.

        Args:
            known_device: Says whether a device is enrolled, so a request from an unknown one is
                refused (the SQLite table's foreign key); None checks nothing.
        """
        self._known_device = known_device
        self._pending: dict[PendingId, PendingConfirmation] = {}
        # Serialises every method, so a settlement's read-decide-write is atomic.
        self._lock = asyncio.Lock()

    async def put(self, pending: PendingConfirmation) -> None:
        """Record a held request; see PendingTable.put."""
        check_new_pending(pending)
        async with self._lock:
            known = self._known_device is None or self._known_device(pending.device_id)
            if pending.id in self._pending or not known:
                raise InvariantViolationError(
                    f"Cannot hold {pending.id}: its id is taken or device {pending.device_id} is "
                    "unknown."
                )
            self._pending[pending.id] = pending

    async def get(self, pending_id: PendingId) -> PendingConfirmation:
        """Return one confirmation; see PendingTable.get."""
        async with self._lock:
            return self._require(pending_id)

    async def settle(
        self, pending_id: PendingId, expected: PendingStatus, settlement: Settlement
    ) -> PendingConfirmation:
        """Settle one confirmation; see PendingTable.settle."""
        async with self._lock:
            settled = settle_pending(self._require(pending_id), expected, settlement)
            self._pending[pending_id] = settled
            return settled

    async def list_by_status(
        self, status: PendingStatus | None = None
    ) -> tuple[PendingConfirmation, ...]:
        """Return confirmations, oldest first; see PendingTable.list_by_status."""
        async with self._lock:
            held = list(self._pending.values())
        matches = [pending for pending in held if status is None or pending.status is status]
        matches.sort(key=lambda pending: (pending.created_at, pending.id))
        return tuple(matches)

    def _require(self, pending_id: PendingId) -> PendingConfirmation:
        """Return a confirmation or raise PendingNotFoundError; the caller holds the lock."""
        pending = self._pending.get(pending_id)
        if pending is None:
            raise PendingNotFoundError(pending_id)
        return pending
