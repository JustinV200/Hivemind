"""Keep the dispatcher's in-memory bookkeeping per notice ref: one lock, and its live recipients.

Two facts about a ref live only in memory. ``RefLocks`` gives each ref in flight one lock, which a
push and a withdrawal of the same ref both hold: a withdrawal that arrives while the original's
deliveries are still retrying waits for them to finish and be recorded, so it reaches every copy
that went out, never an incomplete list. ``LiveRecipients`` remembers which devices a ref reached
over a live socket; live sockets are not persisted, so neither is this, and it is bounded to the
newest ``capacity`` refs (a device whose record was dropped, or that reconnected after a restart,
fetches the current state over its session anyway).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.push.dispatch``. Used
    by ``PushDispatcher`` only. Imports asyncio and waggle's id type only.

Key invariants:
    - A ref's lock exists exactly while some caller holds or waits for it; memory does not grow
      with the number of refs ever pushed.
    - ``LiveRecipients`` never holds more than ``capacity`` refs; the oldest go first.

See Also:
    - hivemind.entrance.push.dispatch.dispatcher for the caller.
"""

from __future__ import annotations

import asyncio
from collections import Counter, OrderedDict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from waggle.ids import DeviceId

__all__ = ["LiveRecipients", "RefLocks"]


class RefLocks:
    """One ``asyncio.Lock`` per ref while anything holds or awaits it."""

    def __init__(self) -> None:
        """Start with no locks."""
        self._locks: dict[str, asyncio.Lock] = {}
        # How many callers hold or wait for each ref's lock; the entry goes at zero.
        self._users: Counter[str] = Counter()

    @asynccontextmanager
    async def hold(self, ref: str) -> AsyncIterator[None]:
        """Hold ``ref``'s lock for the body of an ``async with``.

        Args:
            ref: The notice ref.

        Yields:
            Nothing; the lock is held until the block exits.
        """
        lock = self._locks.setdefault(ref, asyncio.Lock())
        self._users[ref] += 1
        try:
            async with lock:
                yield
        finally:
            # The last user out removes the entry, so refs never pushed again cost nothing.
            self._users[ref] -= 1
            if self._users[ref] == 0:
                del self._users[ref]
                del self._locks[ref]

    def __len__(self) -> int:
        """Return how many refs have a lock right now."""
        return len(self._locks)


class LiveRecipients:
    """Which devices each recent ref reached over a live socket."""

    def __init__(self, capacity: int) -> None:
        """Start empty.

        Args:
            capacity: How many refs to remember; the oldest are forgotten first. At least 1.
        """
        self._capacity = capacity
        self._by_ref: OrderedDict[str, frozenset[DeviceId]] = OrderedDict()

    def add(self, ref: str, devices: frozenset[DeviceId]) -> None:
        """Remember that ``ref`` reached ``devices`` live (adding to any already remembered).

        Args:
            ref: The notice ref.
            devices: The devices a live socket of which took it; nothing is kept when empty.
        """
        if not devices:
            return
        self._by_ref[ref] = self._by_ref.get(ref, frozenset()) | devices
        self._by_ref.move_to_end(ref)
        # Forget the oldest refs beyond capacity; each loop pass drops exactly one.
        while len(self._by_ref) > self._capacity:
            self._by_ref.popitem(last=False)

    def take(self, ref: str) -> frozenset[DeviceId]:
        """Return and forget the devices ``ref`` reached live.

        Args:
            ref: The notice ref being withdrawn.

        Returns:
            The devices; empty when none, or when the ref was forgotten.
        """
        return self._by_ref.pop(ref, frozenset())

    def __len__(self) -> int:
        """Return how many refs are remembered."""
        return len(self._by_ref)
