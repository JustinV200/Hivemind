"""Define LocalPool: the Warden's own count of how many sub-bee slots are in use.

Codingrules section 8.10: "A local pool is everything physically on one Cell... The Cell's Warden
owns its local pool outright... it never asks." `LocalPool` is the v0 slice of that: a bare
capacity counter over the grant's `max_sub_bees` (`GrantIssued.max_sub_bees`, the minimum the
Queen's allocator already computed from the Cell's cores, memory and seats). It owns nothing about
memory, VRAM or seats itself yet -- that is a later roadmap phase's `hosting.py` -- but it is the
one gate `hivemind.wardens.ticks.assign` calls before starting a new sub-bee, so "a Warden refuses
when `max_sub_bees` is reached" is a property of this class, not of scattered counting at every
call site.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package. Owned
    by one `hivemind.wardens.warden.Warden` instance, sized from its current grant's
    `max_sub_bees`; consulted by `hivemind.wardens.ticks.assign` before every spawn and released by
    `hivemind.wardens.ticks.results`/`.alarms` once a sub-bee reaches a terminal state. Calls into
    nothing beyond the standard library.

Key invariants:
    - `acquire()` never lets `in_use` exceed `capacity`: it returns False instead, and the caller
      (never this class) decides what "park the assignment" means.
    - `release()` is idempotent from zero: releasing more than was acquired never drives `in_use`
      negative; it clamps at zero, because a bug that over-releases must not corrupt every later
      caller's view of remaining capacity.

See Also:
    - .claude/codingrules.md section 8.10 for "a Warden divides its local pool under ceilings the
      Queen set once".
    - hivemind.wardens.ticks.assign for the one caller that gates a spawn on `acquire()`.
"""

from __future__ import annotations

__all__ = ["LocalPool"]


class LocalPool:
    """A bare counter of sub-bee slots in use against a fixed capacity.

    Owns its own mutable state in place (codingrules section 8.5): `_in_use` grows on `acquire`
    and shrinks on `release`.
    """

    def __init__(self, max_sub_bees: int) -> None:
        """Build a LocalPool with `max_sub_bees` slots, none in use yet.

        Args:
            max_sub_bees: The most sub-bees this Warden may run at once, from its current grant's
                `max_sub_bees` field.
        """
        self._capacity = max_sub_bees
        self._in_use = 0

    @property
    def capacity(self) -> int:
        """The most sub-bees this pool ever allows at once."""
        return self._capacity

    @property
    def in_use(self) -> int:
        """How many sub-bee slots are currently acquired."""
        return self._in_use

    def acquire(self) -> bool:
        """Take one slot if the pool has room.

        Returns:
            True and increments `in_use` when a slot was free; False, unchanged, when the pool is
            already at `capacity`.
        """
        if self._in_use >= self._capacity:
            return False
        self._in_use += 1
        return True

    def release(self) -> None:
        """Give back one slot, clamped at zero.

        Returns:
            None. Safe to call more times than `acquire()` succeeded; `in_use` never goes negative.
        """
        self._in_use = max(0, self._in_use - 1)

    def resize(self, max_sub_bees: int) -> None:
        """Change this pool's capacity, as a fresh `GrantIssued` revision may.

        Args:
            max_sub_bees: The new capacity; `in_use` is left as-is even if this shrinks below it
                (an over-subscribed pool simply refuses every new `acquire()` until it drains).
        """
        self._capacity = max_sub_bees
