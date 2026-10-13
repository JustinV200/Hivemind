"""Promote a cell-scope tool when its own Warden says so: the remote half of a Warden's promotion.

A Warden in the Hive Stand's process promotes its own Cell's tools straight into the registry; a
Warden on another machine cannot, so it sends the Queen a `tool.promoted` and she writes the row
for it. She never takes the Warden's word for what was proved: the spec and the report come from
the `tool.quarantine` run she brokered and kept, keyed by (tool id, version), and the principal is
built from the link (`WardenPrincipal(warden_id, cell_id)`), never from the message. A hive-scope
`tool.promoted` from a Warden is refused outright (`hive.tool.wrong_principal`): only the Queen
promotes there. A refused promotion is a `control.error` carrying the registry's own stable code;
an accepted one is silent, because the `tool.promoted` event on the trail, and the Nectar that
describes the tool, are the record.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. Called
    by the tools dispatch; calls into `hivemind.royal_jelly` (the registry, the promotion event,
    the Honey link) and `.replies`, `.honey`, `.events`.

Key invariants:
    - The registry is the only way to a promoted row and its refusals are final; this handler adds
      no override and no retry.
    - The principal's Cell is the link's, so a Warden can promote only for its own Cell.
    - A Honey failure after the promotion is logged and never undoes it.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for the principals and the refusals.
    - hivemind.queen.ticks.tools.quarantine for the run whose record this promotes on.
"""

from __future__ import annotations

from collections.abc import Mapping

from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.queen.deps import QuarantineRecord, QueenDeps, WardenLink
from hivemind.queen.ticks.tools import replies
from hivemind.queen.ticks.tools.events import tool_identity
from hivemind.queen.ticks.tools.honey import QueenHoneyDeposit
from hivemind.royal_jelly.errors import PromotionRefusedError
from hivemind.royal_jelly.honey_link import PromotionSite
from hivemind.royal_jelly.promotion.events import promoted_event
from hivemind.royal_jelly.registry import PromotionOffer, WardenPrincipal
from hivemind.supervision.attendant import InboxItem
from waggle.errors import WaggleError
from waggle.ids import WardenId
from waggle.messages.tool import ToolPromoted, ToolScope

DEFAULT_REASON = "Promoted by its Warden after the Quarantine Comb passed it."

log = get_logger(__name__)

__all__ = ["handle_remote_promotion"]


async def handle_remote_promotion(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Promote a Warden's cell-scope tool in the registry on the Queen's kept proof.

    Args:
        deps: The Queen's collaborators; `tool_lane.records` holds the runs she kept.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered item; `item.principal` is the promoting Warden.

    Returns:
        True when `item.payload` was a `ToolPromoted` (accepted, or refused with a
        `control.error`); False otherwise.
    """
    promoted = item.payload
    if not isinstance(promoted, ToolPromoted):
        return False
    link = wardens.get(WardenId(item.principal))
    if link is None:
        return True  # The Warden detached since: nobody to tell.
    refused = (item.id, item.payload_kind)
    problem = _problem(deps, link, promoted)
    if problem is not None:
        await replies.refuse(deps, link, refused, problem[0], problem[1])
        return True
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - `_problem` refused a Hive with no Lab.
    record = deps.tool_lane.records[(promoted.tool_id, promoted.version)]
    offer = PromotionOffer(record.package_sha256, record.report.package_size_bytes)
    principal = WardenPrincipal(link.warden_id, link.cell.id)
    try:
        change = await access.registry.promote(
            record.spec, record.report, offer, principal, promoted.reason or DEFAULT_REASON
        )
    except PromotionRefusedError as exc:
        await replies.refuse(deps, link, refused, exc.code, str(exc))
        return True
    await deps.trail.record(promoted_event(tool_identity(deps), deps.clock, change))
    await _describe(deps, link, record)
    return True


def _problem(deps: QueenDeps, link: WardenLink, promoted: ToolPromoted) -> tuple[str, str] | None:
    """Return `(code, sentence)` when the announcement cannot be acted on, else None."""
    if deps.royal_jelly is None:
        return replies.SANDBOX_UNAVAILABLE, "No Royal Jelly Lab is wired to this Hive."
    if promoted.scope is not ToolScope.CELL:
        return replies.WRONG_PRINCIPAL, "Only the Queen promotes a hive-scope tool."
    if promoted.cell_id != link.cell.id or promoted.promoted_by_warden != link.warden_id:
        return replies.WRONG_PRINCIPAL, "A Warden promotes only for its own Cell, as itself."
    record = deps.tool_lane.records.get((promoted.tool_id, promoted.version))
    if record is None or record.requested_by != link.warden_id:
        return (
            replies.REQUEST_REFUSED,
            f"No Quarantine run of tool {promoted.tool_id} is on record.",
        )
    if promoted.package_sha256 != record.package_sha256 or not record.report.passed:
        return replies.REQUEST_REFUSED, "The announced package is not the one that passed."
    return None


async def _describe(deps: QueenDeps, link: WardenLink, record: QuarantineRecord) -> None:
    """Deposit the tool's Nectar at the promoting Cell; a failure is logged, never raised."""
    site = PromotionSite(
        cell_id=link.cell.id,
        tier=link.cell.comb_shield,
        from_borrowed_cell=link.cell.is_borrowed,
    )
    try:
        await QueenHoneyDeposit(deps).deposit_at(
            record.spec, record.report, record.package_sha256, site
        )
    except (HiveMindError, WaggleError, OSError) as exc:
        log.warning(
            "queen.tools.honey_deposit_failed", tool_id=record.spec.tool_id, error=repr(exc)
        )
