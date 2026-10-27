"""Give the human their Sting Cut lever on the running Queen: cut one Cell off, by Cell or lease.

Roadmap step 13.4a: `hive cells sting-cut <lease|cell>` with step-up is the human's path for any
Cell, the Hive Stand's own included. It arrives from the Hive Entrance (`POST
/v1/cells/{target}/sting-cut`, an interactive device inside its step-up window that typed the
break-glass phrase), through `hivemind.entrance.gate.QueenDoor`, and ends on the one cut path: the
Entrance never writes a Queen table itself. `StingCutDoor` is that method as a mixin
`hivemind.queen.queen.Queen` inherits. A lease id names the Cell its `cell.leased` event recorded
it on; a cited Guard report's trail ids become the cut's evidence.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Called by the Hive Entrance's Sting Cut route through `QueenDoor`.
    Calls into `hivemind.pheromone`, `hivemind.queen.errors`, `hivemind.queen.isolation`
    (IsolationSite) and this sub-package's own modules only; `QueenDeps`, `WardenLink` and
    `HumanInbox` only for their types.

Key invariants:
    - A refused cut raises, with any `guard.denied` already recorded.
    - Nothing here decides for the human: each call is one explicit order, carried out at once.

See Also:
    - hivemind.queen.sting_cut.path for what the lever does.
    - hivemind.entrance.routes.isolation.sting_cut for the route, its step-up and its phrase.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.pheromone import MAX_QUERY_LIMIT, TrailQuery
from hivemind.queen.errors import StingCutRefusedError, UnknownCellError, UnknownGuardReportError
from hivemind.queen.isolation import IsolationSite
from hivemind.queen.sting_cut.order import (
    MAX_STING_CUT_REASON_CHARS,
    Cutter,
    StingCutOrder,
    StingCutOutcome,
)
from hivemind.queen.sting_cut.path import sting_cut_cell
from waggle.ids import CellId, DeviceId, WardenId

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink
    from hivemind.queen.human_inbox import HumanInbox

LEASE_PREFIX = "lease_"  # A target naming a lease rather than a Cell.
LEASED_KIND = "cell.leased"  # The event that records which Cell a lease was opened on.

__all__ = ["LEASE_PREFIX", "StingCutDoor"]


class StingCutDoor:
    """The human's Sting Cut lever, as a method of the running Queen.

    Reads `self._deps`, `self._wardens` and `self._human_inbox`, all set by `Queen.__init__`;
    this class is never instantiated on its own.
    """

    _deps: QueenDeps
    _wardens: dict[WardenId, WardenLink]
    _human_inbox: HumanInbox

    async def sting_cut(
        self, target: str, device_id: DeviceId, reason: str, report_id: str | None = None
    ) -> StingCutOutcome:
        """Cut the Cell `target` names (a Cell id, or a lease on it) off, on the human's order.

        Args:
            target: A `cell_...` id, or a `lease_...` id resolved to its Cell.
            device_id: The enrolled, interactive device the human ordered it from.
            reason: The human's own reason, a short phrase for the trail.
            report_id: A Guard report the order cites; its trail ids become the evidence.

        Returns:
            The outcome: cut, or already cut (nothing changed).

        Raises:
            UnknownCellError: No Cell is known by that id, or no lease by that id was opened.
            UnknownGuardReportError: `report_id` names no Guard request the Queen was sent.
            StingCutRefusedError: The `sting_cut` point refused it (already on the trail).
        """
        cell_id = await self._cell_for(target)
        evidence: tuple[str, ...] = ()
        if report_id is not None:
            filed = await self._deps.guard.requests.get(report_id)
            if filed is None:
                raise UnknownGuardReportError(report_id)
            evidence = tuple(filed.report.event_ids)
        order = StingCutOrder(
            cell_id=cell_id,
            cut_by=Cutter.HUMAN,
            reason=reason[:MAX_STING_CUT_REASON_CHARS],
            report_id=report_id,
            evidence=evidence,
            device_id=device_id,
        )
        site = IsolationSite(
            deps=self._deps, wardens=tuple(self._wardens.values()), human_inbox=self._human_inbox
        )
        outcome = await sting_cut_cell(site, order)
        if outcome.refusal is not None:
            raise StingCutRefusedError(cell_id, f"refused ({outcome.refusal.value})")
        return outcome

    async def _cell_for(self, target: str) -> CellId:
        """Return the Cell `target` names: itself, or the Cell its lease was opened on."""
        if not target.startswith(LEASE_PREFIX):
            return CellId(target)
        leased = await self._deps.trail.query(TrailQuery(kind=LEASED_KIND, limit=MAX_QUERY_LIMIT))
        for event in leased:
            if str(event.payload.get("lease_id")) == target:
                return CellId(event.subject_id)
        raise UnknownCellError(target)
