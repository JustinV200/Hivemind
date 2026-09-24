"""Provide MemoryLoginTable: login failures and known networks in dicts, for tests and demos.

Codingrules 14.4 keeps fakes beside their protocol, honest and production quality. This one holds
each device's consecutive login failures and the networks it has used, in memory under one lock,
applying the same network rule ``SqliteLoginTable`` applies, so the contract suite runs unchanged
over both.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.logins``. Held by
    ``MemoryEntranceStore``. Calls into the protocol's rule only.

Key invariants:
    - Behaves exactly like SqliteLoginTable under the Entrance store contract suite, including
      its refusal (given ``known_device``) of a device the Entrance never enrolled.

See Also:
    - hivemind.entrance.store.logins.protocol for LoginTable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.store.logins.protocol import check_network
from waggle.ids import DeviceId

__all__ = ["MemoryLoginTable"]


class MemoryLoginTable:
    """Consecutive failures and known networks per device, gone when the process exits."""

    def __init__(self, known_device: Callable[[DeviceId], bool] | None = None) -> None:
        """Start with no failures and no networks.

        Args:
            known_device: Says whether a device is enrolled, so nothing is counted or remembered
                for an unknown one (the SQLite tables' foreign keys); None checks nothing.
        """
        self._known_device = known_device
        self._failures: dict[DeviceId, int] = {}
        # Per device: network -> when it was first seen (the sighting kept by remember_network).
        self._networks: dict[DeviceId, dict[str, datetime]] = {}
        # Serialises every method, so a count read and its increment are one step.
        self._lock = asyncio.Lock()

    async def count_failure(self, device_id: DeviceId, at: datetime) -> int:
        """Add one failure; see LoginTable.count_failure."""
        async with self._lock:
            self._require_known(device_id)
            count = self._failures.get(device_id, 0) + 1
            self._failures[device_id] = count
            return count

    async def failures(self, device_id: DeviceId) -> int:
        """Return the count; see LoginTable.failures."""
        async with self._lock:
            return self._failures.get(device_id, 0)

    async def clear_failures(self, device_id: DeviceId) -> None:
        """Reset the count; see LoginTable.clear_failures."""
        async with self._lock:
            self._failures.pop(device_id, None)

    async def networks(self, device_id: DeviceId) -> frozenset[str]:
        """Return the known networks; see LoginTable.networks."""
        async with self._lock:
            return frozenset(self._networks.get(device_id, {}))

    async def remember_network(self, device_id: DeviceId, network: str, at: datetime) -> None:
        """Remember a network; see LoginTable.remember_network."""
        check_network(network)
        async with self._lock:
            self._require_known(device_id)
            self._networks.setdefault(device_id, {}).setdefault(network, at)

    def _require_known(self, device_id: DeviceId) -> None:
        """Refuse a device the Entrance never enrolled; the caller holds the lock."""
        if self._known_device is not None and not self._known_device(device_id):
            raise InvariantViolationError(f"Device {device_id} is not enrolled.")
