"""Define activate, revise, renew_grants_for_warden, revoke and sweep_expired: a grant's own leases.

Roadmap step 4.7: "grants are leases, renewed on the Warden's heartbeat and returned to the pool at
`expires_at` if the Warden is dead or offline; they can be shrunk or revoked; a Warden over its
grant gets an Alarm, not a crash." Every edge this module drives goes through
`hivemind.forage.grant_state.assert_transition`, the one place a `hivemind.forage.ForageGrant`'s
`GrantState` is allowed to move (Appendix C's "Forage grant" row), and every edge that actually
changes state writes its own `forage.*` trail event through `hivemind.queen.trail.
record_forage_event` before the ledger update it describes is considered done. `activate` is this
module's own answer to a gap the state machine's own table leaves open: `hivemind.forage.allocate.
grant` always starts a fresh grant at `GrantState.ISSUED`, and `ISSUED -> ACTIVE` is the only edge
out of it, but nothing upstream of this dispatch ever makes that first move for a
request-driven grant (`hivemind.queen.dispatcher`'s own task-dispatch grants have the same gap,
flagged as pre-existing in this dispatch's own report). Since a Warden's `ForageRequest` is itself
evidence the grant is about to be drawn on immediately, `hivemind.queen.forage.requests` calls
`activate` on every freshly issued request-driven grant before it ever reaches the ledger, so
`revoke`/`sweep_expired` (which only accept ACTIVE or EXHAUSTED, per the table) always have a
legal edge to use once expiry or a heartbeat-loss makes revocation necessary.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by `hivemind.queen.forage.requests` (activate, revise) and
    `hivemind.queen.ticks.liveness` (renew_grants_for_warden, sweep_expired). Calls into
    `hivemind.forage` (ForageGrant, GrantState, assert_transition), `hivemind.queen.deps`
    (QueenDeps), `hivemind.queen.forage.ledger` (ForageLedger) and `hivemind.queen.trail`
    (record_forage_event) only.

Key invariants:
    - Every call that changes a grant's own `GrantState` goes through `assert_transition` first;
      an illegal edge raises `hivemind.forage.InvalidGrantTransitionError` rather than silently
      applying it (codingrules Appendix C, "one transition table... every transition is a trail
      event").
    - `renew_grants_for_warden` never changes a grant's own `GrantState`: extending `expires_at`
      alone is not one of the table's own edges (Appendix C names only ISSUED/ACTIVE/EXHAUSTED/
      REVOKED moves), so no trail event is written for a renewal that changed nothing else.
    - `revoke` and `sweep_expired` both call `hivemind.queen.forage.ledger.ForageLedger.
      record_grant` with the now-REVOKED grant, which removes it from the ledger's own live table
      the same instant it stops counting against headroom.

See Also:
    - .claude/roadmap.md step 4.7 for this module's own field-by-field description.
    - .claude/codingrules.md Appendix C, "Forage grant" row, for the transition table this module
      drives every edge through.
    - .claude/codingrules.md section 8.10 for "grants are leases... a Warden over its grant gets
      an Alarm, not a crash".
    - hivemind.forage.grant_state for GrantState, TRANSITIONS and assert_transition.
    - hivemind.queen.forage.requests for the ForageRequest handling that calls activate/revise.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from hivemind.forage import ForageGrant
from hivemind.forage.grant_state import GrantState, assert_transition
from hivemind.queen.forage.ledger import ForageLedger
from hivemind.queen.trail import record_forage_event
from waggle.ids import WardenId
from waggle.messages.forage.values import RevocationCause

if TYPE_CHECKING:
    # Only for the type hints below: hivemind.queen.deps.QueenDeps carries a ForageLedger field
    # whose own package (hivemind.queen.forage, this module's parent) this module sits inside, so
    # a real (non-TYPE_CHECKING) import here would cycle back through queen/forage/__init__.py.
    from hivemind.queen.deps import QueenDeps

__all__ = ["activate", "renew_grants_for_warden", "revise", "revoke", "sweep_expired"]


def activate(grant: ForageGrant) -> ForageGrant:
    """Move a freshly issued grant from ISSUED to ACTIVE: it is about to be drawn on at once.

    Pure (codingrules section 8.3): no ledger write, no trail event -- the caller
    (`hivemind.queen.forage.requests`) commits the result to the ledger itself, the same
    `record_grant` call every other edge in this module goes through.

    Args:
        grant: A freshly minted grant, normally straight from `hivemind.forage.allocate.grant`
            (which always starts a grant at ISSUED).

    Returns:
        The same grant with `state` moved to ACTIVE.

    Raises:
        hivemind.forage.InvalidGrantTransitionError: `grant.state` is not ISSUED.
    """
    assert_transition(grant.state, GrantState.ACTIVE, subject_id=grant.id)
    return grant.model_copy(update={"state": GrantState.ACTIVE})


async def revise(ledger: ForageLedger, revised: ForageGrant) -> None:
    """Commit a grown or shrunk revision of a live grant to the ledger.

    "Growing or shrinking a grant is a new GrantIssued revision... never itself a GrantState
    change" (ADR-0014): `revised.state` is left exactly as the caller built it (normally
    unchanged from the grant it replaces), so this is a plain ledger upsert, not a transition.

    Args:
        ledger: The Queen's live book.
        revised: The grant's new terms, same id, `revision` incremented by the caller.
    """
    await ledger.record_grant(revised)


async def renew_grants_for_warden(
    ledger: ForageLedger, deps: QueenDeps, holder: WardenId, ttl_s: float
) -> tuple[ForageGrant, ...]:
    """Extend every live grant `holder` currently holds, on its own Heartbeat.

    Args:
        ledger: The Queen's live book.
        deps: The Queen's collaborators; `clock` supplies `now`.
        holder: The Warden whose Heartbeat just arrived.
        ttl_s: Seconds from now until the renewed grants next expire
            (`hivemind.queen.deps.QueenDeps.grant_ttl_s`).

    Returns:
        Every grant renewed, in no particular order (empty when `holder` holds none).
    """
    now = deps.clock.now()
    renewed: list[ForageGrant] = []
    for grant in ledger.grants_for(holder):
        fresh = grant.model_copy(update={"expires_at": now + timedelta(seconds=ttl_s)})
        await ledger.record_grant(fresh)
        renewed.append(fresh)
    return tuple(renewed)


async def revoke(
    ledger: ForageLedger, deps: QueenDeps, grant: ForageGrant, cause: RevocationCause, reason: str
) -> ForageGrant:
    """Move a live grant to REVOKED, record why, and drop it from the ledger's headroom.

    Args:
        ledger: The Queen's live book.
        deps: The Queen's collaborators; `trail`/`clock`/`identity` are what the trail event uses.
        grant: The grant to revoke; must be ACTIVE or EXHAUSTED (the table's only edges into
            REVOKED).
        cause: Which rule revoked it (`waggle.messages.forage.values.RevocationCause`).
        reason: The Queen's own reason, for the trail.

    Returns:
        The same grant with `state` moved to REVOKED.

    Raises:
        hivemind.forage.InvalidGrantTransitionError: `grant.state` is ISSUED (never drawn on) or
            already REVOKED.
    """
    assert_transition(grant.state, GrantState.REVOKED, subject_id=grant.id)
    revoked = grant.model_copy(update={"state": GrantState.REVOKED})
    await ledger.record_grant(revoked)
    await record_forage_event(
        deps,
        "forage.revoked",
        revoked.id,
        holder=revoked.holder,
        cause=cause.value,
        max_sub_bees=revoked.max_sub_bees,
    )
    return revoked


async def sweep_expired(ledger: ForageLedger, deps: QueenDeps) -> tuple[ForageGrant, ...]:
    """Revoke every live grant whose own `expires_at` has passed with no renewal.

    Roadmap step 4.7's own exit criterion: "A Warden whose heartbeat stops has its grant back in
    the pool after expiry." A stopped heartbeat means `renew_grants_for_warden` never runs for
    that Warden again, so its grants' own `expires_at` eventually falls behind `deps.clock.now()`;
    this sweep is what actually returns them to the pool.

    Args:
        ledger: The Queen's live book.
        deps: The Queen's collaborators.

    Returns:
        Every grant this call revoked, in no particular order (empty when nothing has expired).
    """
    now = deps.clock.now()
    expired = [grant for grant in ledger.live_grants() if grant.expires_at <= now]
    revoked: list[ForageGrant] = []
    for grant in expired:
        revoked.append(
            await revoke(
                ledger,
                deps,
                grant,
                RevocationCause.EXPIRED,
                "The expiry passed with no heartbeat renewing it.",
            )
        )
    return tuple(revoked)
