"""Build Sting Cut test data: an in-process Virtual side, and an order for one Cell.

Roadmap step 13.4a's path runs some steps through the Virtual side (`CellCut`): a snapshot kept as
evidence, a scrub its Warden confirms, a forgotten key and a Night Veil teardown. `FakeCellCut`
does each in memory and remembers what it was asked, so a test reads back what the path did and
in which order; `cut_order` builds an order with sensible defaults.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the tests under
    tests/unit/queen/sting_cut and tests/unit/queen/guard_requests.

Key invariants:
    - Every value built here passes the models' own validators.

See Also:
    - hivemind.queen.sting_cut.hands for the seam this fakes.
"""

from __future__ import annotations

from hivemind.cell import Cell
from hivemind.guard import GuardReport
from hivemind.queen.sting_cut import Cutter, ScrubResult, StingCutOrder
from waggle.ids import CellId

__all__ = ["FakeCellCut", "cut_order"]


class FakeCellCut:
    """A `CellCut` over a set of tracked Cells, recording every call in order.

    Attributes:
        tracked: The Cells this fake Virtual side tracks.
        snapshots: Whether `keep_evidence` keeps one (a backend that can snapshot).
        calls: `(method, cell_id)` for every call, in call order.
    """

    def __init__(self, *tracked: CellId, snapshots: bool = True) -> None:
        """Track `tracked`; keep a snapshot of each when `snapshots`."""
        self.tracked = set(tracked)
        self.snapshots = snapshots
        self.calls: list[tuple[str, CellId]] = []

    def tracks(self, cell_id: CellId) -> bool:
        """See `CellCut.tracks`."""
        return cell_id in self.tracked

    async def keep_evidence(self, cell_id: CellId) -> str | None:
        """Keep `snap_<cell>` for a tracked Cell when this fake snapshots."""
        self.calls.append(("keep_evidence", cell_id))
        return f"snap_{cell_id}" if self.snapshots and cell_id in self.tracked else None

    async def scrub(self, cell: Cell) -> ScrubResult:
        """Confirm every scrub at once."""
        self.calls.append(("scrub", cell.id))
        return ScrubResult.CONFIRMED

    async def forget_key(self, cell_id: CellId) -> bool:
        """Forget a tracked Cell's key."""
        self.calls.append(("forget_key", cell_id))
        return cell_id in self.tracked

    async def teardown(self, cell_id: CellId) -> bool:
        """Tear a tracked Cell down, and stop tracking it."""
        self.calls.append(("teardown", cell_id))
        self.tracked.discard(cell_id)
        return True


def cut_order(
    cell_id: CellId, cut_by: Cutter = Cutter.HUMAN, report: GuardReport | None = None
) -> StingCutOrder:
    """Build a Sting Cut order for `cell_id`, citing `report` and its evidence when given.

    Args:
        cell_id: The Cell to cut.
        cut_by: The human by default; the Queen for her own cut.
        report: The Guard report the order answers, if any.

    Returns:
        The order.
    """
    return StingCutOrder(
        cell_id=cell_id,
        cut_by=cut_by,
        reason="Cut for a test.",
        report_id=report.id if report is not None else None,
        evidence=report.event_ids if report is not None else (),
    )
