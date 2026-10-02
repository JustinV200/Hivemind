"""Send an attached Warden its ceilings and hosting plan again until its link takes them.

The Queen sets a Warden's Ceilings and writes its Cell's HostingPlan once, on the first dispatch
that reaches it (`hivemind.queen.dispatcher.ready.assign`), recording both in the ledger before
she sends them. A Warden that re-attaches is sent both again (`hivemind.queen.forage.resend`), but
a send that found the link unable to take it while the Warden stayed attached was never tried
again (phase-7 handoff, section 8 item 5), so a Warden could run every later task with no
ceilings and no plan. Now a refused send leaves the Warden owed (`DecisionBook.owe`), and
`redeliver_owed`, on every dispatch pass, sends each owed Warden still attached what the ledger
holds in force until its link takes it. The Warden stores whatever arrives last
(`hivemind.wardens.ticks.control`), so sending the same revision again is harmless. A Warden that
detaches stops being owed: its re-attach sends both again, and a Cell that is gone never
re-attaches.

No acknowledgement travels back: Waggle has none for these two messages, and none is needed
while a Warden keeps what it was sent for its life and its link is ordered. "Delivered" here
means handed to a live link, as everywhere else the Queen sends (`WardenLink.send`).

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by `hivemind.queen.dispatcher.ready.dispatch` on every pass. Calls into
    `hivemind.common.logging`, `hivemind.queen.forage.ceilings` (resend_ceilings) and
    `hivemind.queen.forage.hosting` (resend_plan) only; `QueenDeps`/`WardenLink` only for their
    types.

Key invariants:
    - Only what the ledger holds in force is sent: never a decision of its own, never a trail
      row (the decision is already on the trail; a resend changes nothing).
    - A Warden stays owed until one pass hands everything in force to its link, or it detaches.

See Also:
    - hivemind.queen.forage.ceilings and .hosting for the sends that mark a Warden owed.
    - hivemind.queen.forage.resend for what a re-attached Warden is sent.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.queen.forage.ceilings import resend_ceilings
from hivemind.queen.forage.hosting import resend_plan

if TYPE_CHECKING:
    # Only for the type hints: see hivemind.queen.forage.grants's module docstring on why
    # QueenDeps/WardenLink cannot be real imports inside hivemind.queen.forage.
    from hivemind.queen.deps import QueenDeps, WardenLink

log = get_logger(__name__)

__all__ = ["redeliver_owed"]


async def redeliver_owed(deps: QueenDeps, wardens: Sequence[WardenLink]) -> None:
    """Resend to every attached Warden still owed its ceilings or plan; forget the detached.

    Args:
        deps: The Queen's collaborators; `ledger.decisions` holds what is in force and who is
            owed.
        wardens: Every Warden currently attached.
    """
    decisions = deps.ledger.decisions
    owed = decisions.owed()
    if not owed:
        return  # The common case: every send got through the first time.
    attached = {link.warden_id: link for link in wardens}
    for warden_id in owed:
        # Settled first: each resend below that its link refuses owes the Warden again.
        decisions.settle(warden_id)
        link = attached.get(warden_id)
        if link is None:
            continue  # Detached: its re-attach sends everything again; a gone Cell never does.
        await resend_ceilings(link, deps)
        await resend_plan(link, deps)
        if warden_id not in decisions.owed():
            log.info("queen.forage.redelivered", warden_id=warden_id, cell_id=link.cell.id)
