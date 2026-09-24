"""Wrap each pending Guard request as the one InboxItem shape the Queen's Attendant scores.

ADR-0035: a Guard request enters the Queen's inbox as `InboxKind.GUARD_REQUEST`, which
`WeightTable.queen_default()` scores at a fixed weight above every Alarm and every human message,
the Guard principal's multiplier on top, with no task link and no latency term, so only age orders
two of them. `guard_items` is the drain her tick runs beside the human's chat
(`hivemind.queen.ticks.chat.human_items`): every request her table still holds undecided becomes
one item, aged from when her door filed it, so a request that waited out a restart keeps its age.
The item carries the report itself as its payload; the decision
(`hivemind.queen.guard_requests.decision`) reads it from there.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    guard_requests sub-package. Called by `hivemind.queen.queen`'s tick. Calls into
    `hivemind.supervision.attendant` (InboxItem, InboxKind, GUARD_PRINCIPAL) and the sub-package's
    own model; `QueenDeps` only for its type.

Key invariants:
    - Every item is GUARD_REQUEST, from `GUARD_PRINCIPAL`, with no severity, task or latency
      budget (the item's own validator refuses anything else).
    - An item's id is its report's, so deciding it stamps exactly that row.

See Also:
    - hivemind.supervision.attendant.weights for the weight and the Guard principal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.queen.guard_requests.model import GuardRequest
from hivemind.supervision.attendant import GUARD_PRINCIPAL, InboxItem, InboxKind

if TYPE_CHECKING:
    # Only for the type hint: hivemind.queen.deps imports this package for GuardDeps.
    from hivemind.queen.deps import QueenDeps

GUARD_REQUEST_PAYLOAD_KIND = "guard.request"  # The local label an item's payload_kind carries.

__all__ = ["GUARD_REQUEST_PAYLOAD_KIND", "guard_inbox_item", "guard_items"]


async def guard_items(deps: QueenDeps) -> list[InboxItem]:
    """Return one InboxItem per Guard request still waiting for the Queen's decision.

    Args:
        deps: The Queen's collaborators; `guard.requests` is read.

    Returns:
        The undecided requests' items, oldest first.
    """
    # One local read, bounded by the store's own page: the Guard Bee caps its own request rate,
    # so a page is more than one tick ever needs.
    pending = await deps.guard.requests.pending()
    return [guard_inbox_item(request) for request in pending]


def guard_inbox_item(request: GuardRequest) -> InboxItem:
    """Wrap one filed request as a GUARD_REQUEST item, aged from when it was filed.

    Args:
        request: A request from the Queen's table.

    Returns:
        A validated InboxItem whose payload is the request's report.
    """
    return InboxItem(
        id=request.id,
        kind=InboxKind.GUARD_REQUEST,
        received_at=request.filed_at,
        principal=GUARD_PRINCIPAL,
        severity=None,
        task_id=None,
        latency_budget_s=None,
        payload_kind=GUARD_REQUEST_PAYLOAD_KIND,
        payload=request.report,
    )
