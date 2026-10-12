"""Tell the Wardens whose Cells can run a promoted tool: ship the blob first, then `tool.promoted`.

A promotion is only useful once the Cells that can run the tool hold its package and know its name.
`QueenAnnouncer` is the promotion flow's `Announcer`: for a hive-scope tool it goes to every
attached Warden whose Cell's operating system the tool supports, for a cell-scope tool to that
Cell's Warden alone. For each it ships the package as a Basket blob (the receiver verifies it,
ADR-0048), and only after the blob is resident sends `tool.promoted` as a fresh announcement
(no correlation: it answers nobody), recording `tool.announced`. The recipients are told in
parallel, so one slow Cell does not hold up the rest, and one failing never stops another.
`announce_current_tools` is the same for a Warden that attaches later: it starts a job that ships
and announces every current hive-scope tool its OS supports, so a Cell that joins after a
promotion is not left without it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside `queen.ticks.tools`. The
    announcer is called by `PromotionFlow` after a promotion; `announce_current_tools` by
    `hivemind.queen.attach.attach_warden`. Calls into `.transfer` (shipping), `.replies`,
    `hivemind.royal_jelly.registry` (the current rows) and `hivemind.queen.deps`.

Key invariants:
    - A Warden is never announced a tool before its package is resident there; if the blob could not
      be shipped it is not announced (and is logged), so no Warden is told of a tool it cannot load.
    - A failure to reach one recipient is logged and never raised; the promotion stands.
    - Sandbox Cells are never announced to.
    - Selection is by the Cell's capabilities (`capabilities.os`), never by its kind.

See Also:
    - docs/adr/0046-tool-spec-comb-registry-and-scopes.md for scopes and `supported_os`.
    - hivemind.royal_jelly.promotion.ports for the `Announcer` protocol this satisfies.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

from hivemind.cell import hive_stand_cell_id
from hivemind.common.errors import HiveMindError
from hivemind.common.logging import get_logger
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.ticks.tools.events import record_tool
from hivemind.queen.ticks.tools.jobs import start_background
from hivemind.queen.ticks.tools.replies import promoted_message, reply
from hivemind.queen.ticks.tools.transfer import ship_blob
from hivemind.royal_jelly.registry import CombToolRow, RegistryChange
from hivemind.royal_jelly.spec import ToolSpec
from waggle.errors import WaggleError
from waggle.ids import WardenId
from waggle.messages.labels import OsFamily
from waggle.messages.tool import ToolPromoted, ToolScope

log = get_logger(__name__)

__all__ = ["QueenAnnouncer", "announce_current_tools", "supports"]


def supports(link: WardenLink, supported_os: Iterable[OsFamily]) -> bool:
    """Return whether `link`'s Cell runs one of `supported_os` (by capability, never kind)."""
    return OsFamily(link.cell.capabilities.os.value) in tuple(supported_os)


class QueenAnnouncer:
    """Announce a promotion to the Wardens it concerns (`Announcer`)."""

    def __init__(self, deps: QueenDeps, wardens: Mapping[WardenId, WardenLink]) -> None:
        """Bind the announcer to the Queen's collaborators and her live Warden table.

        Args:
            deps: The Queen's collaborators.
            wardens: Every Warden attached, keyed by id; read when announcing, so a Warden that
                attached since the job started is seen.
        """
        self._deps = deps
        self._wardens = wardens

    async def announce(self, change: RegistryChange, spec: ToolSpec) -> None:
        """Ship the package to, then announce the promotion to, every Warden that can run it.

        Args:
            change: What the registry did; `change.row` is the new current row.
            spec: The promoted spec; its `supported_os` selects hive-scope recipients.
        """
        row = change.row
        recipients = [
            link
            for link in self._wardens.values()
            if link.cell.id not in self._deps.tool_lane.sandbox_cells and _concerns(link, row, spec)
        ]
        # Concurrently: each recipient's shipment waits on that Warden's acks, not on the others'.
        await asyncio.gather(*(_tell(self._deps, link, row) for link in recipients))


def _concerns(link: WardenLink, row: CombToolRow, spec: ToolSpec) -> bool:
    """Return whether `link`'s Warden should hear of `row`: its own Cell, or a Cell with the OS."""
    if row.scope is ToolScope.CELL:
        return link.cell.id == row.cell_id
    return supports(link, spec.supported_os)


async def announce_current_tools(deps: QueenDeps, link: WardenLink) -> None:
    """Start the job that brings a newly attached Warden up to date on the hive-scope tools.

    Args:
        deps: The Queen's collaborators; `royal_jelly.registry` holds the current rows.
        link: The Warden just attached.
    """
    if deps.royal_jelly is not None:
        # A job, not an await: shipping waits on the Warden's acks, and the attach that calls this
        # may itself be what lets her tick run.
        start_background(deps, _catch_up(deps, link))


async def _catch_up(deps: QueenDeps, link: WardenLink) -> None:
    """Ship and announce every current hive-scope tool `link`'s Cell can run, one at a time."""
    access = deps.royal_jelly
    assert access is not None  # noqa: S101 - `announce_current_tools` checked it.
    try:
        rows = await access.registry.visible_tools(link.cell.id)
    except HiveMindError as exc:
        log.warning("queen.tools.catch_up_failed", warden_id=link.warden_id, code=exc.code)
        return
    for row in rows:
        if row.scope is ToolScope.HIVE and supports(link, row.spec.supported_os):
            await _tell(deps, link, row)


async def _tell(deps: QueenDeps, link: WardenLink, row: CombToolRow) -> None:
    """Ship `row`'s package to one Warden, then announce the tool; log any failure."""
    try:
        shipped = await ship_blob(
            deps, link, row.package_sha256, f"tool {row.name} {row.version} was promoted"
        )
        if not shipped:
            log.warning("queen.tools.announce_unshipped", tool=row.name, warden_id=link.warden_id)
            return
        stand = hive_stand_cell_id(deps.identity.node_id)
        message = promoted_message(row, row.report, stand)
        await _send(deps, link, message)
    except (HiveMindError, WaggleError, OSError, ValueError) as exc:
        # One recipient failing never stops another, and never undoes the promotion.
        log.warning(
            "queen.tools.announce_failed", tool=row.name, warden_id=link.warden_id, error=repr(exc)
        )


async def _send(deps: QueenDeps, link: WardenLink, message: ToolPromoted) -> None:
    """Send the announcement (uncorrelated) and record `tool.announced` with whether it went."""
    delivered = await reply(deps, link, message, None)
    await record_tool(
        deps,
        "tool.announced",
        message.tool_id,
        warden_id=link.warden_id,
        cell_id=link.cell.id,
        name=message.name,
        version=message.version,
        delivered=delivered,
    )
