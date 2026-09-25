"""Take an isolated Cell's access away: revoke its Warden's grants, and write the BLOCK wax.

Two of an isolation's steps (roadmap step 10.6a, ADR-0035) remove access rather than stop work.
`revoke_cell_grants` takes back every shared Forage grant the Cell's Warden holds: each moves to
REVOKED in the Queen's ledger through `hivemind.queen.forage.grants.revoke` (the one place a grant
is revoked, with its `forage.revoked` row; a grant still ISSUED is activated first, because
ISSUED -> REVOKED is not an edge of the grant table), and the Warden is told with
`GrantRevoked`, so it spawns nothing more under it. `block_cell` writes a `BLOCK` Cell Wax note
on the Cell, the placement rule that excludes a Cell outright (rule 3a), so nothing is placed
there while it stays isolated; `unblock_cell` clears it again for the human's lift. The wax is
written by the isolation rule whoever decided the isolation (her rule, her awake episode, or the
human's order), which is why it is AUTOPILOT: the note records the isolation, it does not judge.
A Night Veil Cell gets no note (codingrules 12): Cell Wax outlives the Cell it is about, and
placement never offers a Night Veil Cell to any task but the one it was provisioned for
(`hivemind.queen.dispatcher.snapshot.build_inventory`), which is all the hold its isolation needs;
its isolation itself lives in its own segment and goes with it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    isolation sub-package. Called by `hivemind.queen.isolation.path` and `.lift`. Calls into
    `hivemind.cell` (HoneyClearance), `hivemind.forage`, `hivemind.hive.night_veil`
    (is_night_veil_cell), `hivemind.memory` (the Cell Wax writes), `hivemind.queen.forage.grants`
    (activate, revoke), `hivemind.supervision.attendant`
    (GUARD_PRINCIPAL) and waggle only; `QueenDeps` only for its type.

Key invariants:
    - Every revoked grant has its `forage.revoked` row before the Warden is told.
    - A lost Warden link never stops an isolation: the ledger is the Queen's, and the grant is
      revoked there whatever the Warden hears.

See Also:
    - hivemind.queen.forage.grants for the grant edges; hivemind.memory.cell_wax for the wax.
    - hivemind.queen.placement.rules.excluded_by_block_wax for the rule the wax trips.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.cell import HoneyClearance
from hivemind.forage import ForageGrant
from hivemind.forage.grant_state import GrantState
from hivemind.hive.night_veil import is_night_veil_cell
from hivemind.memory import MemoryContext
from hivemind.memory.cell_wax import (
    MAX_WAX_TEXT_CHARS,
    CellWax,
    WaxProposalInput,
    WaxSeverity,
    WaxState,
    clear_wax,
    propose_wax,
    write_wax,
)
from hivemind.queen.forage.grants import activate, revoke
from hivemind.queen.isolation.order import IsolationOrder, Isolator
from hivemind.supervision.attendant import GUARD_PRINCIPAL
from waggle.envelope import wrap
from waggle.errors import ConnectionLostError, TransportClosedError
from waggle.ids import CellId, GrantId
from waggle.messages.base import MAX_REASON_CHARS
from waggle.messages.cell.wax import WaxDecision, WaxOrigin
from waggle.messages.forage import GrantRevoked
from waggle.messages.forage.values import RevocationCause

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["block_cell", "revoke_cell_grants", "unblock_cell"]


async def revoke_cell_grants(deps: QueenDeps, link: WardenLink, reason: str) -> tuple[GrantId, ...]:
    """Revoke every live grant `link`'s Warden holds, and tell the Warden of each.

    Args:
        deps: The Queen's collaborators; `ledger` holds the grants.
        link: The isolated Cell's Warden.
        reason: Why, naming ids only; it travels on the trail row and the wire message.

    Returns:
        Every grant revoked, in no particular order.
    """
    revoked: list[GrantId] = []
    # Held by the Warden or naming its Cell: a grant is the Warden's access on exactly one Cell.
    held = [
        grant
        for grant in deps.ledger.live_grants()
        if grant.holder == link.warden_id or grant.cell_id == link.cell.id
    ]
    for grant in held:
        taken = await _revoke(deps, grant, reason)
        await _tell_warden(deps, link, taken, reason)
        revoked.append(taken.id)
    return tuple(revoked)


async def block_cell(deps: QueenDeps, order: IsolationOrder) -> str | None:
    """Write a BLOCK Cell Wax note on `order`'s Cell, so placement excludes it outright.

    Not for a Night Veil Cell (codingrules 12): a note would outlive the Cell, and placement never
    offers one to another task anyway (module docstring).

    Args:
        deps: The Queen's collaborators.
        order: The isolation; its report and orderer are named in the note.

    Returns:
        The written note's id, which `cell.isolated` names so the lift clears exactly this one;
        None for a Night Veil Cell, which gets none.
    """
    if await is_night_veil_cell(deps.trail, order.cell_id):
        return None
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    by = "the human" if order.ordered_by is Isolator.HUMAN else "the Queen"
    answering = f" (Guard report {order.report_id})" if order.report_id else ""
    text = f"Isolated by {by}{answering}: nothing is placed here until the human lifts it."
    # Who noticed: the human's own order, else the Guard (or her own escalation policy).
    human = order.ordered_by is Isolator.HUMAN
    proposed = await propose_wax(
        WaxProposalInput(
            cell_id=order.cell_id,
            severity=WaxSeverity.BLOCK,
            text=text[:MAX_WAX_TEXT_CHARS],
            reason=order.reason,
            clearance=HoneyClearance.C1,  # Ids and a fixed sentence: nothing sensitive.
            origin=WaxOrigin.HUMAN if human else WaxOrigin.BEE,
            proposer=None if human else (GUARD_PRINCIPAL if order.report_id else "queen"),
        ),
        MAX_WAX_TEXT_CHARS,
        ctx,
    )
    written = await write_wax(proposed, WaxDecision.AUTOPILOT, "cell isolated", ctx)
    return written.id


async def unblock_cell(
    deps: QueenDeps, cell_id: CellId, wax_id: str | None, reason: str
) -> str | None:
    """Clear the BLOCK note an isolation wrote, if it still stands.

    Args:
        deps: The Queen's collaborators.
        cell_id: The Cell the note is on.
        wax_id: The note `cell.isolated` names; None when it named none.
        reason: Why it is cleared, for the trail.

    Returns:
        The cleared note's id, or None when there was none left WRITTEN.
    """
    if wax_id is None:
        return None
    wax = await _written(deps, cell_id, wax_id)
    if wax is None:
        return None  # Already cleared or expired: the lift has nothing left to clear.
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    await clear_wax(wax, reason, ctx)
    return wax.id


async def _revoke(deps: QueenDeps, grant: ForageGrant, reason: str) -> ForageGrant:
    """Revoke one grant through the one revoke path, activating a never-drawn one first."""
    # ISSUED -> REVOKED is not an edge (Appendix C, "Forage grant"): a grant issued with a task
    # the Warden has not started yet moves to ACTIVE first, the same step the dispatcher takes.
    live = activate(grant) if grant.state is GrantState.ISSUED else grant
    return await revoke(deps.ledger, deps, live, RevocationCause.RECLAIMED, reason)


async def _tell_warden(deps: QueenDeps, link: WardenLink, grant: ForageGrant, reason: str) -> None:
    """Send `GrantRevoked` for `grant`; a closed link is not a reason to stop isolating."""
    message = GrantRevoked(
        grant_id=grant.id,
        holder=grant.holder,
        revision=grant.revision,
        cause=RevocationCause.RECLAIMED,  # The Queen took it back; the reason says why.
        reason=reason[:MAX_REASON_CHARS],
    )
    try:
        await link.transport.send(wrap(message, link.hop, clock=deps.clock))
    except (TransportClosedError, ConnectionLostError):
        return  # The ledger already says REVOKED; a Warden that comes back holds nothing.


async def _written(deps: QueenDeps, cell_id: CellId, wax_id: str) -> CellWax | None:
    """Return the WRITTEN note `wax_id` on `cell_id`, or None."""
    # C2, the Queen's own full allowance, as every Queen-side wax read (placement's snapshot).
    written = frozenset({WaxState.WRITTEN})
    notes = await deps.memory.list_wax(cell_id, written, HoneyClearance.C2)
    return next((note for note in notes if note.id == wax_id), None)
