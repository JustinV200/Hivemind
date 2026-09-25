"""Define set_ceilings and change_ceilings: the Queen's own say over a Warden's local pool.

Roadmap step 4.8: "set and change a Warden's Ceilings (maximum sub-bees, VRAM and disk for
models, scratch disk per lease, resident Basket disk, loadable map entries, exportable seats),
recorded as forage.ceilings_set." Codingrules section 8.10: "the Queen puts a Warden on a device,
or promotes it to a Nuc, she sets Ceilings once... within the ceilings the Warden never asks;
changing a ceiling is a Queen decision on the trail." `set_ceilings` is the first-ever call for a
holder (revision 0); `change_ceilings` is every one after (the old and new values both go on the
trail, per codingrules section 8.10's own "changing a ceiling is a Queen decision on the trail").
Both share one effectful edge (`_apply`): write the ledger's own copy
(`hivemind.forage.Ceilings`, `hivemind.queen.forage.ledger.decisions.DecisionBook`), send
`waggle.messages.forage.CeilingsSet` to the Warden over its `WardenLink` (the same guarded
`link.send(wrap(...))` shape `hivemind.queen.dispatcher._send_grant_and_assign` already uses for
`GrantIssued`; phase-7 handoff open item 8), and record `forage.ceilings_set` -- in that order, so
the trail always shows a ceilings change that has already reached its Warden when it could.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by whatever composition root or later tick first attaches a device Warden
    or promotes one to a Nuc (a later dispatch's own wiring; see this module's report for the
    exact call site still to add). Calls into `hivemind.forage` (Ceilings), `hivemind.queen.deps`
    (QueenDeps, WardenLink), `hivemind.queen.trail` (record_forage_event) and waggle only.

Key invariants:
    - Every call records exactly one `forage.ceilings_set` trail event and sends exactly one
      `CeilingsSet`, mirroring `hivemind.queen.dispatcher`'s own "grant before assignment"
      ordering: the ledger write happens first, then the wire send, then the trail record, so a
      Warden that has already heard about a ceiling change is never left off the audit trail by a
      crash in between.
    - `change_ceilings`'s trail payload always carries both the old and the new `max_sub_bees`,
      the one field small enough (an int) to compare directly on the trail without exceeding
      codingrules section 12's "ids, counts and enums, never text" rule for the rest of a
      `Ceilings`' own shape.

See Also:
    - .claude/roadmap.md step 4.8 for the exact wording this module implements.
    - .claude/codingrules.md section 8.10 for "ceilings, not approvals".
    - hivemind.queen.dispatcher for the WardenLink-send pattern this module mirrors.
    - hivemind.queen.forage.hosting for write_hosting_plan, this module's sibling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.forage import Ceilings
from hivemind.queen.trail import record_forage_event
from waggle.envelope import wrap
from waggle.messages.forage import CeilingsSet

if TYPE_CHECKING:
    # Only for the type hints below: see hivemind.queen.forage.grants's own module docstring note
    # on why QueenDeps/WardenLink cannot be real imports inside hivemind.queen.forage.
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["change_ceilings", "set_ceilings"]


async def set_ceilings(warden: WardenLink, ceilings: Ceilings, deps: QueenDeps) -> None:
    """Set a Warden's Ceilings for the first time (revision 0).

    Args:
        warden: The Warden these ceilings apply to, and its own Waggle link.
        ceilings: The ceilings to set.
        deps: The Queen's collaborators.
    """
    await _apply(warden, ceilings, deps, old=None, reason="First ceilings set for this Warden.")


async def change_ceilings(warden: WardenLink, ceilings: Ceilings, deps: QueenDeps) -> None:
    """Change a Warden's already-set Ceilings.

    Args:
        warden: The Warden these ceilings apply to, and its own Waggle link.
        ceilings: The new ceilings.
        deps: The Queen's collaborators.
    """
    old = deps.ledger.decisions.ceilings_for(warden.warden_id)
    await _apply(
        warden, ceilings, deps, old=old, reason="The Queen changed this Warden's ceilings."
    )


async def _apply(
    warden: WardenLink, ceilings: Ceilings, deps: QueenDeps, *, old: Ceilings | None, reason: str
) -> None:
    """Write, send and record one ceilings edge; the effectful edge both public calls share."""
    revision = await deps.ledger.decisions.record_ceilings(warden.warden_id, ceilings)
    message = CeilingsSet(
        cell_id=warden.cell.id,
        holder=warden.warden_id,
        revision=revision,
        ceilings=ceilings.to_wire(),
        reason=reason,
    )
    # The ledger write above is already durable; an unreachable Warden simply never provisions
    # from this one message and is re-sent nothing else until it is dispatched to again
    # (hivemind.queen.dispatcher.ready._ensure_warden_provisioned only runs once per attachment,
    # a residual gap this dispatch reports rather than widens the scope to fix).
    await warden.send(wrap(message, warden.hop, clock=deps.clock))
    await record_forage_event(
        deps,
        "forage.ceilings_set",
        warden.warden_id,
        cell_id=warden.cell.id,
        revision=revision,
        old_max_sub_bees=old.max_sub_bees if old is not None else None,
        new_max_sub_bees=ceilings.max_sub_bees,
    )
