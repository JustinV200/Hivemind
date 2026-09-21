"""Define LeavingsStore: the Leavings ledger's persistence protocol, and its event guard.

Mirrors `hivemind.brood_chamber.store.protocol.TaskStore`'s own shape (codingrules section 8.1: a
Protocol at every seam; roadmap step 5.0a: "a LeavingsStore protocol and its SQLite store in the
Brood Chamber's store pattern"): a `Leaving` mutation and the `CellEvent` that records it on the
Pheromone Trail commit together, in the same call, or neither commits at all (Appendix C rule 3).
`check_leaving_event` is the one guard both implementations (`hivemind.cell.leavings.store_memory.
InMemoryLeavingsStore`, `hivemind.cell.leavings.store_sqlite.SqliteLeavingsStore`) call before
writing, so a bug inside this package can never file an event under the wrong Cell's `subject_id`
or the wrong event family.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.cell.leavings`.
    Implemented by `hivemind.cell.leavings.store_memory` and `.store_sqlite`; used by
    `hivemind.cell.local.releaser.HiveStandLeaseReleaser.release` (`record_leaving`) and by `hive
    cells leavings list|remove` (`list_leavings`, `get_leaving`, `mark_removed`). Calls into
    `hivemind.cell.leavings.model`, `hivemind.cell.errors` and `hivemind.pheromone` (`CellEvent`)
    only.

Key invariants:
    - `record_leaving`'s event and row commit together, or neither commits at all (Appendix C
      rule 3): a caller can rely on the trail never disagreeing with the store.
    - `record_leaving` upserts: it never raises for an existing row (coordinator review, roadmap
      step 5.0a bug fix -- the same path ledgered twice, most commonly the same goal run twice
      while an earlier run's Leaving is still active, must never fail `release()`). When an
      *active* (not yet removed) row already exists at `(leaving.cell_id, leaving.path)`, every
      field of `leaving` replaces it *except* `prior`, which the existing row's own value always
      wins: `prior` must stay the bytes the path held before any Leaving ever existed there, or
      `hive cells leavings remove` would restore the wrong content. A path whose only row is
      already removed is free to be written again as a wholly fresh row, `leaving.prior` and all
      (`Leaving.removed_at`'s own docstring: a removed row is never reopened).
    - `mark_removed` raises `LeavingNotFoundError` when no row exists at all, and
      `LeavingAlreadyRemovedError` when the one row that exists is already removed: `remove` is
      never silently idempotent (`hivemind.cell.errors.LeavingAlreadyRemovedError`'s own
      docstring).

See Also:
    - .claude/codingrules.md Appendix C rule 3 for the same-transaction rule this protocol
      guarantees, and section 8.1 for the Protocol-at-every-seam rule this module follows.
    - hivemind.brood_chamber.store.protocol for TaskStore, the pattern this module mirrors.
    - hivemind.cell.leavings.model for Leaving, the value this protocol persists.
    - hivemind.cell.leavings.store_memory and .store_sqlite for the two implementations.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from hivemind.cell.errors import LeavingAlreadyRemovedError, LeavingNotFoundError
from hivemind.cell.leavings.model import Leaving
from hivemind.common.errors import InvariantViolationError
from hivemind.pheromone import CellEvent
from waggle.ids import CellId

__all__ = [
    "LeavingAlreadyRemovedError",
    "LeavingNotFoundError",
    "LeavingsStore",
    "check_leaving_event",
]


def check_leaving_event(cell_id: CellId, event: CellEvent) -> None:
    """Require that `event` is well-formed to record alongside a Leavings ledger write.

    Both `LeavingsStore` implementations call this before writing, mirroring
    `hivemind.brood_chamber.store.protocol.check_task_event`.

    Args:
        cell_id: The Cell the write concerns; `event.subject_id` must equal this.
        event: The event about to be recorded.

    Raises:
        InvariantViolationError: `event.subject_id` is not `cell_id`, or `event.family` is not
            `"cell"`.
    """
    # subject_id first: the more common mistake is building the event from the wrong Cell, and
    # this order reports that before the (rarer) wrong-family case.
    if event.subject_id != cell_id:
        raise InvariantViolationError(
            f"event {event.id} has subject_id {event.subject_id!r}, expected cell id {cell_id!r}."
        )
    if event.family != "cell":
        raise InvariantViolationError(
            f"event {event.id} has family {event.family!r}, expected 'cell' for cell {cell_id!r}."
        )


class LeavingsStore(Protocol):
    """Persist Leavings, each mutation atomic with its Pheromone Trail event.

    Implementations (`InMemoryLeavingsStore`, `SqliteLeavingsStore`) must be safe to call
    concurrently.
    """

    async def record_leaving(self, leaving: Leaving, event: CellEvent) -> None:
        """Write `leaving`'s row and record `event`, atomically; upserts, preserving `prior`.

        An active (not yet removed) row already at `(leaving.cell_id, leaving.path)` is replaced
        -- sha256, size, task_id, lease_id, approved_by, reason and left_at all take `leaving`'s
        new values -- but its own `prior` is kept, never `leaving.prior`: `prior` must always be
        the bytes the path held before any Leaving ever existed there, so `hive cells leavings
        remove` still returns the Cell to its true left-as-found state however many times the
        same path is left again before it is ever removed. A row with no active predecessor
        (none at all, or one already removed) is inserted using `leaving.prior` as given.

        Args:
            leaving: The Leaving to store.
            event: The accompanying `cell.left` event.

        Raises:
            InvariantViolationError: `check_leaving_event` rejects `(leaving.cell_id, event)`;
                nothing is written.
        """
        ...

    async def get_leaving(self, cell_id: CellId, path: Path) -> Leaving:
        """Return the active (not yet removed) Leaving at `(cell_id, path)`.

        Args:
            cell_id: The Cell to look under.
            path: The resolved path to look up.

        Returns:
            The matching, still-active Leaving.

        Raises:
            LeavingNotFoundError: No active row exists at `(cell_id, path)`.
        """
        ...

    async def list_leavings(
        self, cell_id: CellId, *, include_removed: bool = False
    ) -> tuple[Leaving, ...]:
        """Return every Leaving recorded for `cell_id`, ordered by `(left_at, path)`.

        Args:
            cell_id: The Cell to list.
            include_removed: When False (the default), only active rows; when True, every row
                this Cell has ever had, active or removed.

        Returns:
            Matching Leavings, ordered by `(left_at, path)` ascending.
        """
        ...

    async def mark_removed(
        self, cell_id: CellId, path: Path, removed_at: datetime, event: CellEvent
    ) -> Leaving:
        """Mark the active Leaving at `(cell_id, path)` removed, and record `event`, atomically.

        The caller replays `prior` (or unlinks) *before* calling this (mirroring
        `hivemind.supervision.capping.apply`'s own "record before write" ordering): a crash
        between the filesystem replay and this call leaves a retryable state (the file is already
        restored; the row still reads as active), never the reverse.

        Args:
            cell_id: The Cell the row belongs to.
            path: The resolved path whose row to mark removed.
            removed_at: When the removal happened.
            event: The accompanying `cell.leaving_removed` event.

        Returns:
            The Leaving, with `removed_at` now set.

        Raises:
            InvariantViolationError: `check_leaving_event` rejects `(cell_id, event)`; nothing is
                written.
            LeavingNotFoundError: No row at all exists at `(cell_id, path)`.
            LeavingAlreadyRemovedError: The row at `(cell_id, path)` is already removed.
        """
        ...
