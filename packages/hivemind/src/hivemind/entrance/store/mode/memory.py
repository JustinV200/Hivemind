"""Provide MemoryModeTable: the Entrance's mode in memory, its events on a trail, for tests.

Codingrules 14.4 keeps fakes beside their protocol, honest and production quality. This one holds
the mode in a field and records each change's event on the Pheromone Trail (the Hive's audit log)
it was built over, before the field changes, so a failed record leaves the mode as it was: the
memory form of "the change commits with its event, or neither does". It applies the same rule
``SqliteModeTable`` applies, so the contract suite runs unchanged over both.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.mode``. Held by
    ``MemoryEntranceStore``. Calls into the protocol's rule and a ``PheromoneTrail``.

Key invariants:
    - Behaves exactly like SqliteModeTable under the Entrance store contract suite.

See Also:
    - hivemind.entrance.store.mode.protocol for ModeTable.
"""

from __future__ import annotations

import asyncio

from hivemind.entrance.errors import EntranceModeConflictError
from hivemind.entrance.reducer import EntranceMode
from hivemind.entrance.store.mode.protocol import check_mode_change
from hivemind.pheromone import GuardEvent, PheromoneTrail

__all__ = ["MemoryModeTable"]


class MemoryModeTable:
    """The Entrance's mode in one field, gone when the process exits."""

    def __init__(self, trail: PheromoneTrail) -> None:
        """Start OPEN, recording every change's event on ``trail``.

        Args:
            trail: Where each change's event is recorded, before the change is applied.
        """
        self._trail = trail
        self._mode = EntranceMode.OPEN
        # Serialises changes, so a read of the mode and the change it decides are one step.
        self._lock = asyncio.Lock()

    async def get(self) -> EntranceMode:
        """Return the mode; see ModeTable.get."""
        async with self._lock:
            return self._mode

    async def change(self, expected: EntranceMode, new: EntranceMode, event: GuardEvent) -> None:
        """Change the mode with its event; see ModeTable.change."""
        check_mode_change(expected, new, event)
        async with self._lock:
            if self._mode is not expected:
                raise EntranceModeConflictError(expected, self._mode)
            # Latency: an in-memory append (or a durable trail's local write); recorded first so
            # a failed record leaves the mode unchanged.
            await self._trail.record(event)
            self._mode = new
