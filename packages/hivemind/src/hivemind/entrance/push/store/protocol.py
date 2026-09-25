"""Define SubscriptionStore: where push subscriptions and the per-ref delivery log persist.

Push subscriptions are per device and persisted (codingrules Appendix C, ADR-0042), and a
withdrawal must reach exactly the subscriptions that received the original notice, which outlives
the process that sent it. So the store holds two things: the subscriptions themselves, and a
delivery log mapping each notice ``ref`` to the subscriptions it reached. Deleting a subscription
deletes its log rows with it, so the log never names a destination that no longer exists.
Implemented by ``hivemind.entrance.push.store.sqlite.SqliteSubscriptionStore`` (the Hive's own
database, its own migration series) and ``hivemind.entrance.push.store.memory.
MemorySubscriptionStore`` (tests and demos); the contract suite runs over both.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.store``. Used by
    ``hivemind.entrance.push.dispatch.PushDispatcher`` only. Imports the push records only.

Key invariants:
    - A subscription id is unique, and so is a device's (channel, endpoint) pair.
    - Every list is ordered by ``created_at`` then id, oldest first.
    - A delivery log row always names a stored subscription: recording one for an id that is no
      longer stored is skipped, and deleting a subscription deletes its rows.

See Also:
    - docs/adr/0042-landing-board-versioning-and-push.md for subscriptions and withdrawal.
    - packages/hivemind/tests/contracts/test_push_subscription_store_contract.py for the contract.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Protocol

from hivemind.entrance.push.models import Subscription, SubscriptionId
from waggle.ids import DeviceId

__all__ = ["SubscriptionStore"]


class SubscriptionStore(Protocol):
    """Persist push subscriptions and which of them each notice ref reached.

    Implementations are safe to call concurrently from one event loop, and every write is atomic.
    """

    async def add(self, subscription: Subscription) -> None:
        """Store a new subscription.

        Args:
            subscription: The subscription, already admitted by the registration gate.

        Raises:
            SubscriptionExistsError: Its id is taken, or its device already has a subscription
                on the same channel and endpoint.
        """
        ...

    async def list_for_device(self, device_id: DeviceId) -> tuple[Subscription, ...]:
        """Return every subscription of one device, oldest first.

        Args:
            device_id: The device.

        Returns:
            Its subscriptions; empty when it has none.
        """
        ...

    async def list_all(self) -> tuple[Subscription, ...]:
        """Return every stored subscription, oldest first.

        Returns:
            All subscriptions; a Hive holds a handful of devices, so this is never large.
        """
        ...

    async def delete(self, subscription_id: SubscriptionId) -> bool:
        """Delete one subscription and its delivery log rows; idempotent.

        Args:
            subscription_id: The subscription to delete.

        Returns:
            True when it existed.
        """
        ...

    async def delete_for_device(self, device_id: DeviceId) -> tuple[SubscriptionId, ...]:
        """Delete every subscription of one device, and their delivery log rows.

        Args:
            device_id: The device leaving APPROVED, or being offboarded.

        Returns:
            The ids deleted, oldest first; empty when it had none.
        """
        ...

    async def record_delivery(
        self, ref: str, subscription_ids: Collection[SubscriptionId], at: datetime
    ) -> None:
        """Record that the notice about ``ref`` reached these subscriptions.

        Recording the same pair twice keeps the first time; an id no longer stored is skipped.

        Args:
            ref: The notice's ref.
            subscription_ids: The subscriptions that accepted it.
            at: When.
        """
        ...

    async def recipients(self, ref: str) -> tuple[Subscription, ...]:
        """Return every stored subscription a notice about ``ref`` reached, oldest first.

        Args:
            ref: The ref of the original notice.

        Returns:
            Its recipients that still exist; empty when none (or when the ref was forgotten).
        """
        ...

    async def forget_ref(self, ref: str) -> int:
        """Delete the delivery log rows of one ref, once it has been withdrawn.

        Args:
            ref: The ref.

        Returns:
            How many rows were deleted.
        """
        ...
