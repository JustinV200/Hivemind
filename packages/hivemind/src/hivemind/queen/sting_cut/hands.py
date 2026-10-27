"""Define CellCut: the Queen's seam for the parts of a Sting Cut only the Virtual side can do.

A Sting Cut (roadmap step 13.4a) does some of its steps through the Queen's own collaborators (her
ledger revokes the grants, her trail records the cut) and some through the Cell's own Warden (the
lease's release kills what it started and removes its scratch). The rest needs the Virtual side:
keeping a snapshot of the Cell as evidence before anything is scrubbed, scrubbing through the
order whose answer only the Cell listener sees, forgetting the Cell's key so its Warden can never
redial, and tearing a Night Veil Cell down so its retained records are purged with it. `CellCut`
is that seam; `hivemind.queen.cell_gate.cut.LifecycleCellCut` is the one implementation, over the
Hive's `CellLifecycle`, and a Hive with no Virtual side has none (every Cell is then Real, and the
path orders its Warden directly).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Carried on `hivemind.queen.guard_requests.GuardDeps.cut`; called by
    `hivemind.queen.sting_cut.path`. Calls into `hivemind.cell` (Cell) and waggle only.

Key invariants:
    - No method raises for a Cell the Virtual side does not track: it answers "nothing done".

See Also:
    - hivemind.queen.cell_gate.cut for the implementation.
"""

from __future__ import annotations

from typing import Protocol

from hivemind.cell import Cell
from hivemind.queen.sting_cut.order import ScrubResult
from waggle.ids import CellId

__all__ = ["CellCut"]


class CellCut(Protocol):
    """What a Sting Cut asks of the Virtual side: evidence, scrub, key, teardown."""

    def tracks(self, cell_id: CellId) -> bool:
        """Return whether `cell_id` is a Virtual Cell this Hive provisioned and still tracks."""
        ...

    async def keep_evidence(self, cell_id: CellId) -> str | None:
        """Snapshot `cell_id` as it stands, before anything is scrubbed.

        Returns:
            The snapshot's id; None when the Cell is not tracked or its backend cannot snapshot.
        """
        ...

    async def scrub(self, cell: Cell) -> ScrubResult:
        """Order `cell`'s Warden to release its lease and wait, bounded, for its answer.

        Returns:
            CONFIRMED when it answered with its scratch gone; UNCONFIRMED otherwise.
        """
        ...

    async def forget_key(self, cell_id: CellId) -> bool:
        """Forget `cell_id`'s key, so no handshake from it verifies again.

        Returns:
            True when the Cell was tracked and its key is forgotten.
        """
        ...

    async def teardown(self, cell_id: CellId) -> bool:
        """Destroy `cell_id`; a Night Veil Cell's retained records are purged with it.

        Returns:
            True when the Cell was tracked and is destroyed.
        """
        ...
