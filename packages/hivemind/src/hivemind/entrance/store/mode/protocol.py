"""Define ModeTable: where the Entrance's mode is persisted, with the event of every change.

The Entrance mode (``OPEN`` or ``REDUCED``, ``hivemind.entrance.reducer``) is persisted in the
Entrance tables so a restart resumes the mode it left (codingrules Appendix C, "Entrance mode"):
an Entrance reduced before a crash comes back reduced. ``ModeTable`` is that one row (codingrules
8.1), implemented by ``SqliteModeTable`` and ``MemoryModeTable``. Every change carries its
``guard.reduced`` or ``guard.reopened`` event and is written with it in one atomic step, from the
mode the caller expected; ``check_mode_change`` is the rule both implementations apply first.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.store.mode``. Used by
    ``hivemind.entrance.reducer.EntranceReducer``; reached through ``EntranceStore.entrance_mode``.
    Calls into ``hivemind.entrance.reducer`` (the modes and their table) and ``hivemind.pheromone``.

Key invariants:
    - The mode is OPEN until a change is persisted.
    - A change is an edge of ``MODE_TRANSITIONS``, from the mode the caller expected, with exactly
      that edge's event; the change and its event commit together or not at all.

See Also:
    - hivemind.entrance.reducer for the machine and the reducer.
    - packages/hivemind/tests/contracts/test_entrance_store_contract.py for the shared contract.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.common.errors import InvariantViolationError
from hivemind.entrance.reducer import EntranceMode, mode_trail_kind
from hivemind.pheromone import GuardEvent

__all__ = ["ModeTable", "check_mode_change"]


class ModeTable(Protocol):
    """Persist the Entrance's mode, and record the event of every change with it."""

    async def get(self) -> EntranceMode:
        """Return the persisted mode.

        Returns:
            The mode last changed to; OPEN when it was never changed.
        """
        ...

    async def change(self, expected: EntranceMode, new: EntranceMode, event: GuardEvent) -> None:
        """Move the mode along one edge, from the mode the caller expected, with its event.

        Args:
            expected: The mode the caller decided from.
            new: The mode to move to.
            event: The edge's ``guard.reduced`` or ``guard.reopened`` event.

        Raises:
            InvalidModeTransitionError: ``expected`` to ``new`` is not an edge.
            InvariantViolationError: ``event`` is not that edge's event.
            EntranceModeConflictError: The persisted mode is not ``expected``.
            DuplicateEventError: ``event``'s id is already on the trail.
        """
        ...


def check_mode_change(expected: EntranceMode, new: EntranceMode, event: GuardEvent) -> None:
    """Refuse a mode change that is no edge, or whose event is not that edge's.

    Args:
        expected: The mode the caller decided from.
        new: The mode it asked for.
        event: The event it gave.

    Raises:
        InvalidModeTransitionError: ``expected`` to ``new`` is not an edge.
        InvariantViolationError: ``event`` is of another kind.
    """
    kind = mode_trail_kind(expected, new)
    if event.kind != kind:
        raise InvariantViolationError(
            f"Event {event.id} is {event.kind}; moving the Entrance from {expected.name} to "
            f"{new.name} must record {kind}."
        )
