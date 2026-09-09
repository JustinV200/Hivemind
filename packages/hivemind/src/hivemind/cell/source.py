"""Define RealCellSource and CellIdentity: how Real Cells are inventoried, leased and opened.

A `RealCellSource` is anything that hands out leases on Real Cells (existing devices the Hive
borrows and leaves exactly as found): `hivemind.cell.local.HiveStandSource` for the machine the
Queen runs on (phase 3 step 3.11), and `hivemind.swarm`'s source for enrolled devices (a later
phase). Every source shares one shape -- list its Cells, lease one, open a session on a lease --
so `queen.placement` and a Warden never need to know which kind of source they are talking to
(codingrules section 8.7: "One abstraction, two sources"). `CellIdentity` is the small, frozen
bundle of "which Hive, which node, which actor" a source stamps on every trail event it writes
(`cell.leased`, `cell.released`), the same role `brood_chamber.chamber.base.ChamberIdentity` plays
for the Brood Chamber.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Implemented by
    `hivemind.cell.fake.FakeCellSource` and `hivemind.cell.local.HiveStandSource` (phase 3 step
    3.11); a later phase adds a Swarm source. Called by queen.placement (Layer 6, to list Cells
    and choose one) and a Warden (Layer 5, to lease and open a session on the Cell it owns).
    Calls into hivemind.cell.lease and hivemind.cell.models and waggle only.

Key invariants:
    - `cells()` never mutates a source's inventory; leasing changes a Cell's availability, never
      the Cell record itself.
    - `lease()` either returns an OPEN RealCellLease or raises LeaseRefusedError; it never leaves
      a half-opened lease behind.

See Also:
    - .claude/codingrules.md section 8.7 for "One abstraction, two sources."
    - .claude/codingrules.md Appendix A.1 for this module's Protocol-and-implementation shape.
    - hivemind.cell.lease for LeaseRequest and RealCellLease, this Protocol's request and result.
    - hivemind.brood_chamber.chamber.base for ChamberIdentity, the pattern CellIdentity mirrors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from hivemind.cell.lease import LeaseRequest, RealCellLease
from hivemind.cell.models import Cell
from hivemind.cell.session import CellSession
from waggle.ids import HiveId, NodeId

__all__ = ["CellIdentity", "RealCellSource"]


@dataclass(frozen=True, slots=True)
class CellIdentity:
    """The Hive, node and actor a RealCellSource stamps on every trail event it writes.

    Mirrors `hivemind.brood_chamber.chamber.base.ChamberIdentity`: a source has no Hive Manifest
    to read this from yet (phase 3), so it is injected at construction instead.

    Attributes:
        hive_id: The Hive this source's events belong to.
        node_id: This process's own node id (normally the Queen's), carried on every event so a
            merged trail can tell which node recorded it.
        actor: Who this source acts as: a bee id, or the literal "system"
            (`hivemind.pheromone.events.base.ACTOR_LITERALS`).
    """

    hive_id: HiveId
    node_id: NodeId
    actor: str


class RealCellSource(Protocol):
    """List, lease and open sessions on the Real Cells one backend knows about.

    Implementations must be safe to call concurrently: a Warden may lease while another lists.
    """

    @property
    def name(self) -> str:
        """This source's name, the value every Cell it hands out carries as `source`."""
        ...

    async def cells(self) -> tuple[Cell, ...]:
        """Return every Cell this source currently knows about.

        Returns:
            This source's Cell inventory, in no particular order.
        """
        ...

    async def lease(self, request: LeaseRequest) -> RealCellLease:
        """Open a lease on the Cell `request` names.

        Args:
            request: Which Cell, held by which Warden, at which access level, with which paths
                allowed outside scratch.

        Returns:
            An OPEN RealCellLease with its own scratch directory.

        Raises:
            LeaseRefusedError: `request.cell_id` names no Cell this source holds, that Cell is
                already leased, or this source is disabled.
        """
        ...

    async def open_session(self, lease: RealCellLease) -> CellSession:
        """Open a terminal session on the Cell `lease` was opened on.

        Args:
            lease: An OPEN lease this source itself issued.

        Returns:
            A CellSession scoped to `lease`'s scratch directory and allowed paths.
        """
        ...
