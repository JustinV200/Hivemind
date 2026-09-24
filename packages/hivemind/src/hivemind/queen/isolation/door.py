"""Give the human their two isolation levers on the running Queen: isolate a Cell, and lift it.

ADR-0035 leaves two things to the human alone: isolating the Hive Stand's own lease (the Queen may
isolate any other Cell herself, never that one) and lifting any isolation. Both arrive from the
Hive Entrance (`POST /v1/cells/{cell_id}/isolate` and `/lift`, an interactive device inside its
step-up window), through `hivemind.entrance.gate.QueenDoor`, and end on the one isolation path and
the one lift: the Entrance never writes a Queen table itself. `IsolationDoor` is those two methods
as a mixin `hivemind.queen.queen.Queen` inherits, over the Queen's own collaborators, her attached
Wardens and her human inbox. A human's isolation may cite a Guard report the Queen was sent (the
Hive Stand fallback's CRITICAL Alarm names one): its evidence is then where the Cell's memory stops
being trusted, exactly as when she isolates on it herself.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by the Hive Entrance's isolation routes through `QueenDoor`.
    Calls into `hivemind.queen.errors` and this sub-package's own modules only; `QueenDeps`,
    `WardenLink` and `HumanInbox` only for their types.

Key invariants:
    - An isolation the `isolation` point refused raises, with `guard.denied` already recorded.
    - Nothing here decides for the human: each call is one explicit order, carried out at once.

See Also:
    - hivemind.queen.isolation.path and .lift for what each lever does.
    - hivemind.entrance.routes.isolation for the routes and their step-up.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.queen.errors import IsolationRefusedError, UnknownGuardReportError
from hivemind.queen.isolation.lift import lift_isolation
from hivemind.queen.isolation.order import (
    MAX_ISOLATION_REASON_CHARS,
    IsolationOrder,
    IsolationOutcome,
    Isolator,
    LiftOutcome,
)
from hivemind.queen.isolation.path import isolate_cell
from hivemind.queen.isolation.site import IsolationSite
from waggle.ids import CellId, DeviceId, WardenId

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink
    from hivemind.queen.human_inbox import HumanInbox

__all__ = ["IsolationDoor"]


class IsolationDoor:
    """The human's isolate and lift levers, as methods of the running Queen.

    Reads `self._deps`, `self._wardens` and `self._human_inbox`, all set by `Queen.__init__`;
    this class is never instantiated on its own.
    """

    _deps: QueenDeps
    _wardens: dict[WardenId, WardenLink]
    _human_inbox: HumanInbox

    async def isolate_cell(
        self,
        cell_id: CellId,
        device_id: DeviceId,
        reason: str,
        report_id: str | None = None,
    ) -> IsolationOutcome:
        """Isolate `cell_id` on the human's order: the only way to isolate the Hive Stand.

        Args:
            cell_id: The Cell to isolate.
            device_id: The enrolled, interactive device the human ordered it from.
            reason: The human's own reason, a short phrase for the trail.
            report_id: A Guard report the order cites; its evidence dates the taint.

        Returns:
            The outcome: isolated, or already isolated (nothing changed).

        Raises:
            UnknownCellError: No attached Warden runs the Cell.
            UnknownGuardReportError: `report_id` names no Guard request the Queen was sent.
            IsolationRefusedError: The `isolation` point refused it (already on the trail).
        """
        order = IsolationOrder(
            cell_id=cell_id,
            ordered_by=Isolator.HUMAN,
            reason=reason[:MAX_ISOLATION_REASON_CHARS],
            report_id=report_id,
            evidence=await self._cited_evidence(report_id),
            device_id=device_id,
        )
        outcome = await isolate_cell(self._isolation_site(), order)
        if outcome.refusal is not None:
            why = f"the isolation point refused it ({outcome.refusal.value})"
            raise IsolationRefusedError(cell_id, why)
        return outcome

    async def lift_isolation(self, cell_id: CellId, device_id: DeviceId) -> LiftOutcome:
        """Lift `cell_id`'s isolation and the Queen's holds on it; tainted memory stays tainted.

        Args:
            cell_id: The Cell to lift.
            device_id: The enrolled, interactive device the human lifted it from.

        Returns:
            What the lift did.

        Raises:
            CellNotIsolatedError: Nothing stood to lift; nothing changed.
        """
        return await lift_isolation(self._isolation_site(), cell_id, device_id)

    def _isolation_site(self) -> IsolationSite:
        """The Queen as an isolation runs against her: collaborators, Wardens, human inbox."""
        return IsolationSite(
            deps=self._deps, wardens=tuple(self._wardens.values()), human_inbox=self._human_inbox
        )

    async def _cited_evidence(self, report_id: str | None) -> tuple[str, ...]:
        """Return the cited report's trail evidence, oldest first; none when nothing is cited."""
        if report_id is None:
            return ()
        filed = await self._deps.guard.requests.get(report_id)
        if filed is None:
            raise UnknownGuardReportError(report_id)
        return tuple(filed.report.event_ids)
