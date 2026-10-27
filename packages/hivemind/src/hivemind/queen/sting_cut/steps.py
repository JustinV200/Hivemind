"""Carry out a Sting Cut's steps: follow an isolation, revoke the grants, scrub, forget the key.

The one cut path (`hivemind.queen.sting_cut.path`, roadmap step 13.4a) runs these in order once
the `sting_cut` point allowed it. `isolation_first` makes every cut follow an isolation rather
than replace one: the Queen cuts only a Cell an isolation already stands on, and the human's cut
isolates first when none stands (through the one isolation path, so its BLOCK wax, paused bees and
tainted memory hold for the cut Cell too). `revoke_grants` takes back every grant on the Cell,
through its Warden when one is attached, else straight from the ledger. `scrub` has the Cell's own
Warden release its lease, which kills every process the lease started and removes its scratch:
through the Virtual side's bounded, confirmed order for a Cell it tracks, else as a plain order
to a Real Cell's Warden, whose answer is its own to send. `forget_key` forgets a Virtual Cell's key
and closes its link, so the session ends now and no redial verifies again; a Real Cell's Warden
lives on, holding no lease, no grant, and nothing placed on its isolated Cell.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    sting_cut sub-package. Called by `.path`. Calls into `hivemind.forage`, `hivemind.queen.forage`
    (activate, revoke), `hivemind.queen.isolation`, the sub-package's own modules and waggle only.

Key invariants:
    - A lost Warden link never stops a cut: the ledger is the Queen's, and every step that needs
      the link says what it could not do instead of raising.

See Also:
    - hivemind.wardens.ticks.control.handle_release_lease for the Warden's half of the scrub.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.forage.grant_state import GrantState
from hivemind.queen.forage.grants import activate, revoke
from hivemind.queen.isolation import (
    IsolationOrder,
    IsolationSite,
    IsolationState,
    Isolator,
    isolate_cell,
    read_isolation,
)
from hivemind.queen.isolation.access import revoke_cell_grants
from hivemind.queen.sting_cut.hands import CellCut
from hivemind.queen.sting_cut.order import Cutter, ScrubResult, StingCutOrder
from waggle.envelope import wrap
from waggle.ids import CellId, EventId, GrantId
from waggle.messages.forage.values import RevocationCause
from waggle.messages.supervision import Intervene, InterventionAction

if TYPE_CHECKING:
    # Only for the type hints: every hivemind.queen sub-package keeps QueenDeps type-only.
    from hivemind.queen.deps import QueenDeps, WardenLink

__all__ = ["forget_key", "isolation_first", "revoke_grants", "scrub"]


async def isolation_first(site: IsolationSite, order: StingCutOrder) -> tuple[bool, EventId | None]:
    """Return whether the cut may go on, and the `cell.isolated` event it follows, if any.

    Args:
        site: The running Queen's collaborators and attached Wardens.
        order: The cut.

    Returns:
        `(False, None)` when the Queen ordered it and no isolation stands; otherwise True and the
        standing isolation (the human's own, made now when a Warden is attached to isolate through).
    """
    standing = await read_isolation(site.deps, order.cell_id)
    if standing.state is IsolationState.ISOLATED and standing.isolated is not None:
        return True, EventId(standing.isolated.id)
    if order.cut_by is Cutter.QUEEN:
        return False, None  # Roadmap 13.4a: her cut follows an isolation, never replaces one.
    if site.link_for(order.cell_id) is None:
        return True, None  # No Warden to isolate through: the cut alone cuts it off.
    isolation = IsolationOrder(
        cell_id=order.cell_id,
        ordered_by=Isolator.HUMAN,
        reason=order.reason,
        report_id=order.report_id,
        evidence=order.evidence,
        device_id=order.device_id,
    )
    outcome = await isolate_cell(site, isolation)
    return True, outcome.event_id


async def revoke_grants(
    deps: QueenDeps, link: WardenLink | None, cell_id: CellId, reason: str
) -> tuple[GrantId, ...]:
    """Revoke every live grant on `cell_id`: through its Warden when attached, else in the ledger.

    Args:
        deps: The Queen's collaborators; `ledger` holds the grants.
        link: The Cell's Warden, when attached (it is told of each revocation).
        cell_id: The Cell being cut.
        reason: Why, naming ids only.

    Returns:
        Every grant revoked.
    """
    if link is not None:
        return await revoke_cell_grants(deps, link, reason)
    revoked: list[GrantId] = []
    for grant in [g for g in deps.ledger.live_grants() if g.cell_id == cell_id]:
        # ISSUED -> REVOKED is not an edge (Appendix C): a never-drawn grant is activated first.
        live = activate(grant) if grant.state is GrantState.ISSUED else grant
        taken = await revoke(deps.ledger, deps, live, RevocationCause.RECLAIMED, reason)
        revoked.append(taken.id)
    return tuple(revoked)


async def scrub(
    deps: QueenDeps, cut: CellCut | None, link: WardenLink | None, reason: str
) -> ScrubResult:
    """Have the Cell's Warden release its lease: kill what it started, remove its scratch.

    Args:
        deps: The Queen's collaborators.
        cut: The Virtual side, if the Hive has one.
        link: The Cell's Warden, when attached.
        reason: Why, for the Warden's own record.

    Returns:
        How far the release was confirmed.
    """
    if link is None:
        return ScrubResult.UNREACHED
    if cut is not None and cut.tracks(link.cell.id):
        return await cut.scrub(link.cell)  # Bounded, and confirmed by the Cell's own answer.
    message = Intervene(
        action=InterventionAction.RELEASE_LEASE,
        subject=None,  # Always the recipient itself (waggle spec section 8.3).
        task_id=None,
        slot=None,
        binding=None,
        alarm_id=None,
        reason=reason,
    )
    sent = await link.send(wrap(message, link.hop, clock=deps.clock))
    return ScrubResult.ORDERED if sent else ScrubResult.UNREACHED


async def forget_key(cut: CellCut | None, link: WardenLink | None, cell_id: CellId) -> bool:
    """Forget a Virtual Cell's key and close its link, so its session ends and never resumes.

    Args:
        cut: The Virtual side, if the Hive has one.
        link: The Cell's Warden, when attached.
        cell_id: The Cell being cut.

    Returns:
        True when the key is forgotten; False for a Cell the Virtual side does not track.
    """
    if cut is None or not await cut.forget_key(cell_id):
        return False
    if link is not None:
        # The handshake is where a key is checked: an open link would outlive the forgetting.
        await link.transport.close()
    return True
