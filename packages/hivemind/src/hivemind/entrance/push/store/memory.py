"""Provide MemorySubscriptionStore: push subscriptions and their delivery log in dicts.

Codingrules 14.4 keeps fakes beside their protocol, honest and production quality. This one holds
the push subscriptions and the per-ref delivery log in memory and applies the same rules the SQLite
schema enforces (a unique id, a unique (device, channel, endpoint) triple, log rows that disappear
with their subscription, a log row only for a stored subscription), so the contract suite runs
unchanged over both implementations.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.store``. Used by
    tests, demos and any composition root that wants push without a database file. Imports the
    push records and errors only.

Key invariants:
    - Behaves exactly like ``SqliteSubscriptionStore`` under
      ``tests/contracts/test_push_subscription_store_contract.py``.
    - No lock is needed: no method awaits between reading and writing, so on one event loop no
      two calls can interleave.

See Also:
    - hivemind.entrance.push.store.protocol for SubscriptionStore.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import datetime

from hivemind.entrance.push.errors import SubscriptionExistsError
from hivemind.entrance.push.models import Subscription, SubscriptionId
from waggle.ids import DeviceId

__all__ = ["MemorySubscriptionStore"]


class MemorySubscriptionStore:
    """The push tables in two dicts, gone when the process exits."""

    def __init__(self) -> None:
        """Create empty tables."""
        self._subscriptions: dict[SubscriptionId, Subscription] = {}
        # ref -> (subscription id -> when it was first delivered there).
        self._deliveries: dict[str, dict[SubscriptionId, datetime]] = {}

    async def add(self, subscription: Subscription) -> None:
        """Store a new subscription; see SubscriptionStore.add."""
        # The same two uniqueness rules the SQLite schema declares.
        for stored in self._subscriptions.values():
            if stored.id == subscription.id or _same_destination(stored, subscription):
                raise SubscriptionExistsError(subscription.id, subscription.device_id)
        self._subscriptions[subscription.id] = subscription

    async def list_for_device(self, device_id: DeviceId) -> tuple[Subscription, ...]:
        """Return one device's subscriptions; see SubscriptionStore.list_for_device."""
        return _ordered(s for s in self._subscriptions.values() if s.device_id == device_id)

    async def list_all(self) -> tuple[Subscription, ...]:
        """Return every subscription; see SubscriptionStore.list_all."""
        return _ordered(self._subscriptions.values())

    async def delete(self, subscription_id: SubscriptionId) -> bool:
        """Delete one subscription and its log rows; see SubscriptionStore.delete."""
        existed = self._subscriptions.pop(subscription_id, None) is not None
        # The cascade the SQLite foreign key performs.
        for delivered in self._deliveries.values():
            delivered.pop(subscription_id, None)
        return existed

    async def delete_for_device(self, device_id: DeviceId) -> tuple[SubscriptionId, ...]:
        """Delete one device's subscriptions; see SubscriptionStore.delete_for_device."""
        doomed = tuple(s.id for s in await self.list_for_device(device_id))
        for subscription_id in doomed:
            await self.delete(subscription_id)
        return doomed

    async def record_delivery(
        self, ref: str, subscription_ids: Collection[SubscriptionId], at: datetime
    ) -> None:
        """Record a ref's recipients; see SubscriptionStore.record_delivery."""
        delivered = self._deliveries.setdefault(ref, {})
        # Only stored subscriptions, and the first time wins, as INSERT OR IGNORE does.
        for subscription_id in subscription_ids:
            if subscription_id in self._subscriptions:
                delivered.setdefault(subscription_id, at)

    async def recipients(self, ref: str) -> tuple[Subscription, ...]:
        """Return a ref's recipients; see SubscriptionStore.recipients."""
        delivered = self._deliveries.get(ref, {})
        return _ordered(self._subscriptions[sid] for sid in delivered if sid in self._subscriptions)

    async def forget_ref(self, ref: str) -> int:
        """Delete a ref's log rows; see SubscriptionStore.forget_ref."""
        return len(self._deliveries.pop(ref, {}))


def _same_destination(first: Subscription, second: Subscription) -> bool:
    """Return whether two subscriptions share a device, a channel and an endpoint."""
    return (first.device_id, first.channel, first.endpoint) == (
        second.device_id,
        second.channel,
        second.endpoint,
    )


def _ordered(subscriptions: Iterable[Subscription]) -> tuple[Subscription, ...]:
    """Sort subscriptions oldest first, by id within one instant, as the SQLite store does."""
    return tuple(sorted(subscriptions, key=lambda s: (s.created_at, s.id)))
