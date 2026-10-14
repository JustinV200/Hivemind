"""Define resend_state: send a re-attached Warden what the Forage ledger says it holds, again.

A Warden's Forage terms come to it as messages: the ceilings the Queen set once (`CeilingsSet`),
the hosting plan she wrote for its Cell (`PlanWritten`) and each grant she issued (`GrantIssued`).
Until a Warden could reconnect, each was sent once per Queen life and nothing noticed when a
Warden missed one: `hivemind.queen.dispatcher.ready.assign._ensure_warden_provisioned` skips a
Warden the ledger already holds ceilings for, so a Warden that dropped its link, restarted, or was
met for the first time by a restarted Queen (whose restored ledger remembers it) ran on whatever
it last remembered, or on nothing (roadmap 11.9, ADR-0053: "re-sends the current grants, ceilings
and hosting plan"). `resend_state` is the delivery of what is already decided: the ledger's current
ceilings, its Cell's current plan and every grant it still holds that has not lapsed, in that
order (the Warden reads its grants against its ceilings). It decides nothing and records nothing
itself; the attach path counts what it sent in `warden.reconnected`. A Warden the ledger holds
nothing for (a brand-new one) gets nothing here, and gets its first ceilings and plan with its first
dispatch as it always did.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by `hivemind.queen.attach.admit.attach_warden` for every Warden it admits.
    Calls into `hivemind.queen.forage.ceilings`, `.grants` and `.hosting`, `hivemind.forage`
    (UnknownSourceError), `hivemind.queen.deps` (type only) and waggle only.

Key invariants:
    - Nothing is written to the ledger and no Forage event is recorded: this only re-delivers.
    - A grant whose `expires_at` has passed, or one the ledger no longer holds live, is not sent:
      a Warden is never re-issued what the Queen has already taken back.
    - A grant naming a source the Forage map no longer carries is skipped, not fatal: the others
      are still sent, and the Warden finds out the usual way that source is gone.
    - A send to a link that has already gone fails quietly (`WardenLink.send`): the count reports
      only what was handed to the link.

See Also:
    - docs/adr/0053-nucs-warden-migration-and-offline-operation.md for the rule.
    - hivemind.queen.forage.ceilings.resend_ceilings and .hosting.resend_plan for the first two.
    - hivemind.queen.attach.admit for the caller and `warden.reconnected`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.forage import UnknownSourceError
from hivemind.queen.forage.ceilings import resend_ceilings
from hivemind.queen.forage.grants import grant_message
from hivemind.queen.forage.hosting import resend_plan
from waggle.envelope import wrap

if TYPE_CHECKING:
    # Only for the type hints below: see hivemind.queen.forage.grants's own module docstring note
    # on why QueenDeps/WardenLink cannot be real imports inside hivemind.queen.forage.
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["Resent", "resend_state"]


@dataclass(frozen=True, slots=True)
class Resent:
    """What `resend_state` handed to a re-attached Warden's link.

    Attributes:
        ceilings: Whether its ceilings were sent.
        plan: Whether its Cell's hosting plan was sent.
        grants: How many live grants were sent.
    """

    ceilings: bool
    plan: bool
    grants: int


async def resend_state(deps: QueenDeps, link: WardenLink) -> Resent:
    """Send `link`'s Warden its current ceilings, hosting plan and live grants, in that order.

    Args:
        deps: The Queen's collaborators; her ledger holds what is current.
        link: The Warden that just attached, and its link.

    Returns:
        What was sent: whether the ceilings and the plan went, and how many grants.
    """
    ceilings = await resend_ceilings(link, deps)
    plan = await resend_plan(link, deps)
    return Resent(ceilings=ceilings, plan=plan, grants=await _resend_grants(deps, link))


async def _resend_grants(deps: QueenDeps, link: WardenLink) -> int:
    """Send every unexpired live grant `link`'s Warden holds; return how many reached the link."""
    now = deps.clock.now()
    sent = 0
    for grant in deps.ledger.grants_for(link.warden_id):
        if grant.expires_at <= now:
            continue  # Lapsed: the next liveness sweep takes it back, so it is not re-issued.
        try:
            message = await grant_message(deps, grant)
        except UnknownSourceError:
            continue  # Its source left the Forage map since it was issued; the rest still go.
        if await link.send(wrap(message, link.hop, clock=deps.clock)):
            sent += 1
    return sent
