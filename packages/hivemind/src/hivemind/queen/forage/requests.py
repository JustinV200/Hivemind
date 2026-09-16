"""Define ForageRequestOutcome and handle_sub_bee_request: answer a Warden's ForageRequest.

Roadmap step 4.7: "`ForageRequest` handled by an autopilot rule within headroom, by awake when
contested; `forage.granted`/`forage.denied` with the reason." This module is the business logic
behind that: it reads `hivemind.queen.forage.ledger.ForageLedger.headroom`, builds the
`hivemind.queen.autopilot.ForageRequestSignal` that rule reads, and -- only for a `GRANT` verdict
-- commits a revised `hivemind.forage.ForageGrant` to the ledger through `hivemind.queen.forage.
grants.revise`. It never sends a wire message or writes a trail event itself: `hivemind.queen.
ticks.forage` is the one caller, and it owns both (the same split `hivemind.queen.dispatcher`
already keeps between minting a grant and sending it). v1 only evaluates `SUB_BEES` requests
against real ledger headroom (`hivemind.queen.forage.ledger.model.Headroom`'s own module docstring
explains why the ledger carries no shared spend or per-source seat ceiling to size the other three
`ForageRequestKind` members against yet); a `SHARED_SEATS`, `SPEND` or `BINDING` request is denied
with a reason naming that scope, never silently mishandled.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by `hivemind.queen.ticks.forage`, once per received `ForageRequest`. Calls
    into `hivemind.forage` (ForageGrant), `hivemind.queen.autopilot` (ForageAutopilotOutcome,
    ForageRequestSignal, decide_forage_request), `hivemind.queen.deps` (QueenDeps),
    `hivemind.queen.forage.grants` (revise) and `hivemind.queen.forage.ledger` (ForageLedger)
    only.

Key invariants:
    - `handle_sub_bee_request` never mutates the ledger on a DENY or NEEDS_JUDGEMENT verdict:
      only `ForageAutopilotOutcome.GRANT` calls `grants.revise`.
    - The revised grant's `max_sub_bees` is the existing grant's plus the wanted delta
      (`waggle.messages.forage.values.ForageDelta.sub_bees` is "extra... wanted", an increment,
      never a replacement), so a grant only ever grows through this path; shrinking a grant is a
      Queen-initiated `hivemind.queen.forage.grants.revise` call this module does not make.

See Also:
    - .claude/roadmap.md step 4.7 for this module's own field-by-field description.
    - .claude/codingrules.md section 8.14 for "contested Forage" running at high effort -- the
      caller's own choice, not this module's.
    - hivemind.queen.autopilot.forage for the pure rule this module builds the signal for.
    - hivemind.queen.forage.ledger.model for Headroom's own v1 scope (sub-bees only).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.forage import ForageGrant
from hivemind.queen.autopilot import (
    ForageAutopilotOutcome,
    ForageRequestSignal,
    decide_forage_request,
)
from hivemind.queen.forage import grants
from hivemind.queen.forage.ledger import ForageLedger
from waggle.ids import GrantId
from waggle.messages.forage import ForageRequest as WireForageRequest
from waggle.messages.forage.values import ForageRequestKind as WireForageRequestKind

if TYPE_CHECKING:
    # Only for the type hint below: see hivemind.queen.forage.grants's own module docstring note
    # on why QueenDeps cannot be a real import inside hivemind.queen.forage.
    from hivemind.queen.deps import QueenDeps

__all__ = ["ForageRequestOutcome", "handle_sub_bee_request"]

# The reason a non-SUB_BEES request is always denied in v1 (module docstring's own scope note).
_OUT_OF_SCOPE_REASON = (
    "v1 evaluates only SUB_BEES requests against the ledger's own headroom; SHARED_SEATS, SPEND "
    "and BINDING requests have no configured shared ceiling to compute headroom from yet."
)


@dataclass(frozen=True, slots=True)
class ForageRequestOutcome:
    """What `handle_sub_bee_request` decided, and the grant if it granted one.

    Attributes:
        autopilot_outcome: GRANT, DENY or NEEDS_JUDGEMENT
            (`hivemind.queen.autopilot.ForageAutopilotOutcome`); the caller
            (`hivemind.queen.ticks.forage`) decides what to do about NEEDS_JUDGEMENT, since this
            module never builds the awake decision itself.
        grant: The grant's fresh terms, set only when `autopilot_outcome` is GRANT.
        reason: Why, for the trail and the wire `ForageReply`.
    """

    autopilot_outcome: ForageAutopilotOutcome
    grant: ForageGrant | None
    reason: str


async def handle_sub_bee_request(
    ledger: ForageLedger, deps: QueenDeps, wire_request: WireForageRequest
) -> ForageRequestOutcome:
    """Decide, and if granted commit, one Warden's ForageRequest.

    Args:
        ledger: The Queen's live book.
        deps: The Queen's collaborators; unused directly today, carried for the same reason every
            other `hivemind.queen.forage` entry point takes it -- a future dimension (spend,
            tokens) will need `deps.clock`/`deps.reserve` the way `grants.py` already does.
        wire_request: The Warden's own request.

    Returns:
        The decision, and the revised grant when granted.
    """
    if wire_request.kind is not WireForageRequestKind.SUB_BEES:
        return ForageRequestOutcome(ForageAutopilotOutcome.DENY, None, _OUT_OF_SCOPE_REASON)

    existing = ledger.grant(wire_request.grant_id)
    if existing is None:
        return ForageRequestOutcome(
            ForageAutopilotOutcome.DENY,
            None,
            f"Grant {wire_request.grant_id} is not known to the ledger; nothing to extend.",
        )

    wanted = wire_request.wanted.sub_bees
    headroom = ledger.headroom()
    signal = ForageRequestSignal(
        within_headroom=wanted <= headroom.sub_bees,
        shrinkable=_shrinkable(ledger, existing.id, wanted, headroom.sub_bees),
    )
    outcome = decide_forage_request(signal)
    if outcome is not ForageAutopilotOutcome.GRANT:
        return ForageRequestOutcome(outcome, None, _reason(outcome, wanted, headroom.sub_bees))

    revised = existing.model_copy(
        update={"max_sub_bees": existing.max_sub_bees + wanted, "revision": existing.revision + 1}
    )
    await grants.revise(ledger, revised)
    return ForageRequestOutcome(
        ForageAutopilotOutcome.GRANT,
        revised,
        f"{wanted} more sub-bees granted from headroom ({headroom.sub_bees} free before this).",
    )


def _shrinkable(ledger: ForageLedger, exclude: GrantId, wanted: int, free: int) -> bool:
    """Return whether shrinking every OTHER live grant to zero could cover the rest of `wanted`.

    A coarse over-approximation deliberately: this only decides whether the request is worth an
    awake episode's judgement (`hivemind.queen.autopilot.ForageAutopilotOutcome.NEEDS_JUDGEMENT`),
    never which grant to actually shrink -- that policy call is exactly what queen.awake does not
    yet support (this dispatch's own report names the gap).
    """
    other = sum(g.max_sub_bees for g in ledger.live_grants() if g.id != exclude)
    return (free + other) >= wanted


def _reason(outcome: ForageAutopilotOutcome, wanted: int, free: int) -> str:
    """Explain a DENY or NEEDS_JUDGEMENT verdict, for the trail and the wire ForageReply."""
    if outcome is ForageAutopilotOutcome.NEEDS_JUDGEMENT:
        return (
            f"Wants {wanted} sub-bees against {free} free; cannot be met from headroom alone "
            "but shrinking another live grant could -- contested, needs judgement."
        )
    return f"Wants {wanted} sub-bees against {free} free, and no other live grant to shrink."
