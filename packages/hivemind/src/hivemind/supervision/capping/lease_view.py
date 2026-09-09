"""Define LeaseView: the slice of a Real Cell lease the Capping gate needs, as a Protocol.

`hivemind.cell.RealCellLease` owns a lease's scratch root, its allowed paths outside scratch, and
the bookkeeping `release()` later restores from (codingrules section 8.7: "Owned versus leased").
Capping needs to read that bookkeeping and add to it while applying a proposal, but `cell` sits
below `supervision` in the layer table (codingrules section 4), so this package may depend on
`cell`'s public names -- yet a `RealCellLease` is a mutable object with far more on it than Capping
touches. `LeaseView` names exactly the slice this gate uses, as a `typing.Protocol` (codingrules
section 8.1: "Protocols at every seam"), so `hivemind.cell.RealCellLease` satisfies it structurally
without this package importing that concrete class at all: a lease-shaped test double
(`tests/builders/capping.FakeLeaseView`) is just as valid a caller as the real thing.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the supervision package. Read
    and written by `hivemind.supervision.capping.checks.deterministic.PathAllowlistCheck` and
    `hivemind.supervision.capping.apply`; implemented by `hivemind.cell.RealCellLease` (once its
    `note_restore_path` method lands from a concurrent roadmap dispatch) and by
    `tests/builders/capping.FakeLeaseView` for tests. Calls into nothing beyond the standard
    library.

Key invariants:
    - This module, and nothing else in `hivemind.supervision.capping`, imports
      `hivemind.cell.RealCellLease` -- doing so would tie the gate to one lease implementation
      when the whole point of a Protocol seam is that it does not need to (docs/adr/
      0018-capping-gate-postconditions-and-risk-tiers.md).
    - `note_restore_path` records what to restore; it does not perform the restore itself, and it
      is synchronous because it only appends to in-memory bookkeeping the lease's own `release()`
      later replays, the same reason `RealCellLease.note_started_process` is synchronous.

See Also:
    - .claude/codingrules.md section 8.1 for the Protocol-at-every-seam rule this module follows.
    - .claude/codingrules.md section 8.7 for "Owned versus leased" and what `release()` restores.
    - docs/adr/0018-capping-gate-postconditions-and-risk-tiers.md for why this seam is a Protocol
      rather than a direct dependency on `hivemind.cell.RealCellLease`.
    - hivemind.supervision.capping.apply for `apply_action`, which calls `note_restore_path` and
      `note_touched_path` before writing outside scratch.
    - hivemind.supervision.capping.checks.deterministic for `PathAllowlistCheck`, which calls
      `is_path_allowed`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

__all__ = ["LeaseView"]


class LeaseView(Protocol):
    """The slice of a Real Cell lease the Capping gate needs to check and apply a proposal.

    `hivemind.cell.RealCellLease` satisfies this structurally once its `note_restore_path` method
    lands (a concurrent roadmap dispatch); implementations must be safe to call concurrently, the
    same requirement `RealCellLease` itself already carries.
    """

    @property
    def scratch_root(self) -> Path:
        """This lease's scratch directory; every relative path in a proposal resolves against it."""
        ...

    @property
    def allowed_paths(self) -> tuple[Path, ...]:
        """Paths outside scratch this lease may also touch."""
        ...

    def is_path_allowed(self, path: Path) -> bool:
        """Return whether `path`, once resolved, is reachable from this lease.

        Args:
            path: An already-resolved (absolute, `..`-free) path to check.

        Returns:
            True if `path` is inside `scratch_root` or under one of `allowed_paths`.
        """
        ...

    async def note_touched_path(self, path: Path) -> None:
        """Record that this lease's session touched `path`, for the audit trail and release().

        Args:
            path: The path that was touched, already resolved.
        """
        ...

    def note_restore_path(self, path: Path, prior: bytes | None) -> None:
        """Record what `path` held before an outside-scratch write, so release() can restore it.

        Args:
            path: The path about to be written, already resolved.
            prior: The bytes `path` held before the write, or None when `path` did not exist
                before it (so `release()` knows to delete it rather than restore old content).
        """
        ...
