"""Install a promoted tool: verify its package is resident, then re-attenuate every running bee.

A promotion changes what a Worker can call only when its own Warden says so (ADR-0046: "a
promotion changes a running Worker's bundle only when its Warden re-attenuates it"). `install_tool`
is that step. Given a `tool.promoted` -- the Queen's announcement or the answer to a request, or
this Warden's own promotion for its Cell -- it first checks the package is really held and still
hashes to the digest the announcement names (a Warden never vouches for bytes it cannot read), then
records the tool in the Warden's own table of entries by name (a later version replaces the
earlier), and finally replaces the Comb bundle of every sub-bee whose capability set allows
`tool:<name>` and every capability the tool needs. The Worker's bounded loop sees the bundle's new
version on its next round and offers the tool; the Worker records its own `tool.loaded`, which is
the trail row for the hand-over, so this module logs at info and records no event of its own.
A package that is not resident is a `tool.load_refused` row and the tool is handed to nobody.
`retract_tool` is the undoing, for a CELL-scope promotion this Warden announced and installed on
the Queen's silence that she then refused after the wait: the table gets back what the promotion
displaced (or loses the name), every bee is re-attenuated, and a `tool.load_refused` row with the
Queen's code says why the tool went away.

Fits into the Hive:
    Layer 5 (per-Cell supervisors; spawn and supervise Workers), inside the wardens package's
    ticks.tools sub-package. Called by `relay` for a Queen-sent promotion (and her late refusal
    of one this Warden announced) and by the cell-scope announcer for this Warden's own. Calls
    into `hivemind.royal_jelly.promotion` (the trail row), `pending` (the announcement),
    `hivemind.wardens.spawn.comb` (the attenuation), `hivemind.workers.tools.comb` (the entry) and
    `hivemind.wardens.ticks.basket.source` (the residency check).

Key invariants:
    - A tool is installed only if its package is held and hashes to `package_sha256`; otherwise
      nothing changes, a `tool.load_refused` is recorded and False is returned.
    - A CELL-scope tool is installed only for the Cell it names; another Cell's is refused.
    - A sub-bee's bundle is replaced whole with entries its own set allows: never widened.

See Also:
    - docs/adr/0046 and docs/adr/0048 for the rules, and .claude/roadmap.md steps 9.6 and 9.7.
    - hivemind.workers.tools.comb.entry for the bundle and its `replace`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.common.logging import get_logger
from hivemind.royal_jelly.promotion import tool_event
from hivemind.wardens.spawn.comb import visible_entries
from hivemind.wardens.ticks.basket.source import blob_verified
from hivemind.wardens.ticks.tools.messages import tool_identity
from hivemind.wardens.ticks.tools.pending import Announced
from hivemind.workers.tools.comb import CombToolEntry
from waggle.messages.control import ErrorMessage
from waggle.messages.tool.authoring import ToolPromoted, ToolScope

if TYPE_CHECKING:
    from hivemind.wardens.warden import Warden

NOT_RESIDENT = "not_resident"  # The code of a refusal because the package is not held here.
WRONG_CELL = "wrong_cell"  # ...because a CELL-scope tool names another Cell.
RETRACTED = "retracted"  # ...because the Queen refused the promotion after it was installed.

__all__ = [
    "NOT_RESIDENT",
    "RETRACTED",
    "WRONG_CELL",
    "install_tool",
    "reattenuate",
    "record_load_refused",
    "retract_tool",
]

log = get_logger(__name__)


async def install_tool(warden: Warden, message: ToolPromoted) -> bool:
    """Make `message`'s tool available to this Warden's sub-bees, if its package is here.

    Args:
        warden: The installing Warden; its entries table and every sub-bee's bundle are changed.
        message: The `tool.promoted` to install.

    Returns:
        True when the tool is now in the Warden's table and in the bundle of every sub-bee that
        may call it; False (with a `tool.load_refused` recorded) when it was not installed.
    """
    entry = CombToolEntry.from_promoted(message)
    refusal = await _refusal(warden, message)
    if refusal is not None:
        code, reason = refusal
        await record_load_refused(warden, entry, code, reason)
        return False
    # A later version of a name replaces the earlier: a rollback is a promotion of an older one.
    warden._comb_entries[entry.name] = entry
    handed = reattenuate(warden)
    log.info(
        "wardens.tool_installed",
        warden_id=warden._warden_id,
        tool=entry.name,
        version=entry.version,
        offered_to=handed,
    )
    return True


async def retract_tool(warden: Warden, announced: Announced, error: ErrorMessage) -> None:
    """Take back a promotion the Queen refused after this Warden had installed it.

    Args:
        warden: The Warden whose entries table and sub-bees' bundles are changed.
        announced: The promotion as announced, and the entry it displaced in the table.
        error: The Queen's late `control.error`; its code goes on the trail row.
    """
    entry = CombToolEntry.from_promoted(announced.promoted)
    current = warden._comb_entries.get(entry.name)
    # Only the version the Queen refused comes out: a later promotion of the name is its own.
    if current is not None and current.version == entry.version:
        if announced.displaced is None:
            del warden._comb_entries[entry.name]
        else:
            warden._comb_entries[entry.name] = announced.displaced
        reattenuate(warden)
    reason = f"the Queen refused the promotion after it was installed ({error.code})."
    await record_load_refused(warden, entry, RETRACTED, reason)


def reattenuate(warden: Warden) -> int:
    """Replace each sub-bee's bundle with what its own capability set allows; return how many.

    Args:
        warden: The Warden whose entries table and sub-bees are read.

    Returns:
        The number of sub-bees whose bundle now holds at least one tool.
    """
    cell = warden._cell
    if cell is None:
        return 0
    held = 0
    for sub_bee in warden._sub_bees.values():
        if sub_bee.comb_tools is None:
            continue  # Spawned with no Basket behind it: it is offered no Comb tools at all.
        offered = visible_entries(warden._comb_entries.values(), sub_bee.capabilities, cell.id)
        sub_bee.comb_tools.replace(offered)
        held += 1 if offered else 0
    return held


async def _refusal(warden: Warden, message: ToolPromoted) -> tuple[str, str] | None:
    """Return `(code, reason)` for a promotion that must not be installed, else None."""
    cell = warden._cell
    if message.scope is ToolScope.CELL and (cell is None or message.cell_id != cell.id):
        return WRONG_CELL, f"tool {message.name} is for Cell {message.cell_id}, not this Warden's."
    if not await blob_verified(warden._deps, message.package_sha256):
        return NOT_RESIDENT, f"package {message.package_sha256} is not resident or does not verify."
    return None


async def record_load_refused(warden: Warden, entry: CombToolEntry, code: str, reason: str) -> None:
    """Record `tool.load_refused` for a tool this Warden would not hand to anyone, and log it.

    Args:
        warden: The refusing Warden.
        entry: The tool refused; the row names its hash, never its bytes.
        code: A stable code (`not_resident`, `wrong_cell`).
        reason: One sentence for the trail.
    """
    deps = warden._deps
    log.warning(
        "wardens.tool_load_refused", tool=entry.name, code=code, sha256=entry.package_sha256
    )
    # Built with `tool_event` rather than `load_refused_event` so the row names the hash refused.
    event = tool_event(
        tool_identity(deps),
        deps.clock,
        "tool.load_refused",
        entry.tool_id,
        name=entry.name,
        version=entry.version,
        code=code,
        reason=reason,
        package_sha256=entry.package_sha256,
    )
    await deps.trail.record(event)
