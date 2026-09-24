"""Define IsolationSite and alert_human: where an isolation runs, and how the human hears of it.

An isolation (roadmap step 10.6a, ADR-0035) needs three things only the running Queen holds
together: her collaborators, the Wardens attached to her right now (the isolated Cell's own
Warden relays the pause and hears of the revoked grant), and her inbox of Alarms waiting on the
human. `IsolationSite` bundles them for the one path, the lift and the Queen's decision on a Guard
request. `alert_human` is how any of them tells the human: a SECURITY Alarm raised by the Hive
itself, escalated straight to the chain's last hop and pushed to every enrolled device, through
the one showing path (`hivemind.queen.guard_requests.show_alert`), which shows an Alarm about a
Guard report at most once, whichever path reaches the human about it first.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Built by `hivemind.queen.isolation.door` and the Queen's Guard request
    decision. Calls into `hivemind.queen.guard_requests` (SecurityAlert, show_alert) and waggle
    only; `QueenDeps`, `WardenLink` and `HumanInbox` only for their types.

Key invariants:
    - Every Alarm it raises is SECURITY, HANDLING, at clearance C1, originated by the Hive.
    - At most one Alarm per Guard report reaches the human, whichever path raises it.

See Also:
    - hivemind.queen.guard_requests.show for the one showing path.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.queen.guard_requests import SecurityAlert, show_alert
from waggle.ids import CellId

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink
    from hivemind.queen.human_inbox import HumanInbox

__all__ = ["IsolationSite", "alert_human"]


@dataclass(frozen=True, slots=True)
class IsolationSite:
    """What an isolation runs against: the Queen's collaborators, Wardens and human inbox.

    Attributes:
        deps: The Queen's collaborators.
        wardens: Every Warden attached to her now, in attachment order.
        human_inbox: Her inbox of Alarms waiting on the human.
    """

    deps: QueenDeps
    wardens: Sequence[WardenLink]
    human_inbox: HumanInbox

    def link_for(self, cell_id: CellId) -> WardenLink | None:
        """Return the attached Warden that runs `cell_id`, or None.

        Args:
            cell_id: The Cell.

        Returns:
            Its Warden's link, when one is attached.
        """
        return next((link for link in self.wardens if link.cell.id == cell_id), None)


async def alert_human(site: IsolationSite, alert: SecurityAlert) -> bool:
    """Tell the human about `alert` with a SECURITY Alarm, pushed to every device, once a report.

    Args:
        site: Where the isolation or decision ran.
        alert: What to tell the human; an alert about a report already shown is not shown again.

    Returns:
        True when this call showed it.
    """
    return await show_alert(site.deps, site.human_inbox, alert)
