"""Define ForageRequestOutcome and handle_forage_request_for_kind: answer a Warden's ForageRequest.

Roadmap step 4.7: "`ForageRequest` handled by an autopilot rule within headroom, by awake when
contested; `forage.granted`/`forage.denied` with the reason." This module is the business logic
behind that: it reads `hivemind.queen.forage.ledger.ForageLedger.headroom`, builds the
`hivemind.queen.autopilot.ForageRequestSignal` that rule reads, and -- only for a `GRANT` verdict
-- commits a revised `hivemind.forage.ForageGrant` to the ledger through `hivemind.queen.forage.
grants.revise`. It never sends a wire message or writes a trail event itself: `hivemind.queen.
ticks.forage` is the one caller, and it owns both (the same split `hivemind.queen.dispatcher`
already keeps between minting a grant and sending it). Roadmap step 4.7 shipped only `SUB_BEES`
against real ledger headroom; step 4.8 (this module's own dispatch) adds `SHARED_SEATS` (checked
against `ForageLedger.headroom().shared_seats`, growing the existing grant's own
`SeatReservation` for the named source) and `SPEND` (checked against `ForageLedger.spend.
headroom`, using `deps.budgets.spend_cap_usd`, never itself contested -- see `_handle_spend`'s own
docstring for why). `BINDING` is still denied with a reason: codingrules section 8.10's routing
(phase 8) is what picks a higher-grade binding, not the ledger.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's forage
    sub-package. Called by `hivemind.queen.ticks.forage`, once per received `ForageRequest`. Calls
    into `hivemind.forage` (ForageGrant, SeatReservation), `hivemind.queen.autopilot`
    (ForageAutopilotOutcome, ForageRequestSignal, decide_forage_request), `hivemind.queen.deps`
    (QueenDeps), `hivemind.queen.forage.grants` (revise), `hivemind.queen.forage.ledger`
    (ForageLedger) and `hivemind.queen.intake` (goal_spend_cap: a SPEND request is measured
    against the goal's own budget when its request set a lower one, roadmap step 10.5) only.

Key invariants:
    - `handle_forage_request_for_kind` never mutates the ledger on a DENY or NEEDS_JUDGEMENT
      verdict: only `ForageAutopilotOutcome.GRANT` calls `grants.revise`.
    - Every dimension this module grants only ever grows the existing grant: `max_sub_bees`,
      `spend_budget` and the named source's `SeatReservation.seats` are all incremented by the
      wanted delta (`waggle.messages.forage.values.ForageDelta`'s own docstring: "wanted", never a
      replacement); shrinking a grant is a Queen-initiated `hivemind.queen.forage.grants.revise`
      call this module does not make.

See Also:
    - .claude/roadmap.md step 4.7 for this module's own field-by-field description.
    - .claude/roadmap.md step 4.8 for the SHARED_SEATS and SPEND checks this dispatch adds.
    - .claude/codingrules.md section 8.14 for "contested Forage" running at high effort -- the
      caller's own choice, not this module's.
    - hivemind.queen.autopilot.forage for the pure rule this module builds the signal for.
    - hivemind.queen.forage.ledger.model for Headroom's own two dimensions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hivemind.brood_chamber import TaskNotFoundError
from hivemind.forage import ForageGrant
from hivemind.forage.models.grants import SeatReservation
from hivemind.queen.autopilot import (
    ForageAutopilotOutcome,
    ForageRequestSignal,
    decide_forage_request,
)
from hivemind.queen.forage import grants
from hivemind.queen.forage.ledger import ForageLedger
from hivemind.queen.intake import goal_spend_cap
from waggle.ids import TaskId
from waggle.messages.forage import ForageRequest as WireForageRequest
from waggle.messages.forage.values import ForageRequestKind as WireForageRequestKind

if TYPE_CHECKING:
    # Only for the type hint below: see hivemind.queen.forage.grants's own module docstring note
    # on why QueenDeps cannot be a real import inside hivemind.queen.forage.
    from hivemind.queen.deps import QueenDeps

__all__ = ["ForageRequestOutcome", "grant_wanted", "handle_forage_request_for_kind"]

# BINDING stays out of scope through phase 8's own routing (module docstring).
_BINDING_OUT_OF_SCOPE_REASON = (
    "BINDING requests pick a higher-grade binding for a slot, which is routing's job "
    "(codingrules section 8.10, phase 8), not a ledger headroom check."
)
_SPEND_NEEDS_GOAL_REASON = "SPEND requests must name a task_id: the goal whose spend cap applies."
_SEATS_NEED_SOURCE_REASON = "SHARED_SEATS requests must name a source_id."


@dataclass(frozen=True, slots=True)
class ForageRequestOutcome:
    """What `handle_forage_request_for_kind` decided, and the grant if it granted one.

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


async def handle_forage_request_for_kind(
    ledger: ForageLedger, deps: QueenDeps, wire_request: WireForageRequest
) -> ForageRequestOutcome:
    """Decide, and if granted commit, one Warden's ForageRequest.

    Renamed from `handle_sub_bee_request` (roadmap step 4.7's own leftover, flagged in that
    dispatch's report): the name it shipped under named only the dimension step 4.7 shipped first,
    which step 4.8's SHARED_SEATS/SPEND additions already outgrew.

    Args:
        ledger: The Queen's live book.
        deps: The Queen's collaborators; `budgets.spend_cap_usd` sizes a SPEND request's headroom.
        wire_request: The Warden's own request.

    Returns:
        The decision, and the revised grant when granted.
    """
    existing = ledger.grant(wire_request.grant_id)
    if existing is None:
        return ForageRequestOutcome(
            ForageAutopilotOutcome.DENY,
            None,
            f"Grant {wire_request.grant_id} is not known to the ledger; nothing to extend.",
        )

    # One branch per WireForageRequestKind member (waggle.messages.forage.values); BINDING is the
    # only one with no ledger-side check yet (module docstring).
    if wire_request.kind is WireForageRequestKind.SUB_BEES:
        return await _handle_sub_bees(ledger, existing, wire_request)
    if wire_request.kind is WireForageRequestKind.SHARED_SEATS:
        return await _handle_shared_seats(ledger, existing, wire_request)
    if wire_request.kind is WireForageRequestKind.SPEND:
        return await _handle_spend(ledger, deps, existing, wire_request)
    return ForageRequestOutcome(ForageAutopilotOutcome.DENY, None, _BINDING_OUT_OF_SCOPE_REASON)


async def _handle_sub_bees(
    ledger: ForageLedger, existing: ForageGrant, wire_request: WireForageRequest
) -> ForageRequestOutcome:
    """Grow `existing.max_sub_bees` within the ledger's sub-bee headroom, or deny/defer."""
    wanted = wire_request.wanted.sub_bees
    headroom = ledger.headroom()
    other = sum(g.max_sub_bees for g in ledger.live_grants() if g.id != existing.id)
    outcome = decide_forage_request(
        ForageRequestSignal(
            within_headroom=wanted <= headroom.sub_bees,
            shrinkable=_shrinkable(other, wanted, headroom.sub_bees),
        )
    )
    if outcome is not ForageAutopilotOutcome.GRANT:
        return ForageRequestOutcome(
            outcome, None, _reason(outcome, "sub-bees", wanted, headroom.sub_bees)
        )
    revised = existing.model_copy(
        update={"max_sub_bees": existing.max_sub_bees + wanted, "revision": existing.revision + 1}
    )
    await grants.revise(ledger, revised)
    return ForageRequestOutcome(
        ForageAutopilotOutcome.GRANT,
        revised,
        f"{wanted} more sub-bees granted from headroom ({headroom.sub_bees} free before this).",
    )


async def _handle_shared_seats(
    ledger: ForageLedger, existing: ForageGrant, wire_request: WireForageRequest
) -> ForageRequestOutcome:
    """Grow `existing`'s SeatReservation on the wanted source, within shared-seat headroom."""
    source_id = wire_request.wanted.source_id
    if source_id is None:
        return ForageRequestOutcome(ForageAutopilotOutcome.DENY, None, _SEATS_NEED_SOURCE_REASON)

    wanted = wire_request.wanted.seats
    headroom = ledger.headroom()
    other = sum(
        seat.seats
        for grant in ledger.live_grants()
        if grant.id != existing.id
        for seat in grant.seats
    )
    outcome = decide_forage_request(
        ForageRequestSignal(
            within_headroom=wanted <= headroom.shared_seats,
            shrinkable=_shrinkable(other, wanted, headroom.shared_seats),
        )
    )
    if outcome is not ForageAutopilotOutcome.GRANT:
        return ForageRequestOutcome(
            outcome, None, _reason(outcome, "shared seats", wanted, headroom.shared_seats)
        )
    revised = existing.model_copy(
        update={
            "seats": _grow_seat_reservation(existing.seats, source_id, wanted),
            "revision": existing.revision + 1,
        }
    )
    await grants.revise(ledger, revised)
    return ForageRequestOutcome(
        ForageAutopilotOutcome.GRANT,
        revised,
        f"{wanted} more seats on {source_id} granted from headroom "
        f"({headroom.shared_seats} free before this).",
    )


async def _handle_spend(
    ledger: ForageLedger, deps: QueenDeps, existing: ForageGrant, wire_request: WireForageRequest
) -> ForageRequestOutcome:
    """Grow `existing.spend_budget` within the requesting goal's remaining spend cap.

    Never NEEDS_JUDGEMENT: a goal's spend cap (`deps.budgets.spend_cap_usd`) is not a shared pool
    another live grant could be shrunk to relieve -- each goal has its own cap, so "contested"
    has no meaning here the way it does for sub-bees or shared seats (module docstring).
    """
    goal_id: TaskId | None = wire_request.task_id
    if goal_id is None:
        return ForageRequestOutcome(ForageAutopilotOutcome.DENY, None, _SPEND_NEEDS_GOAL_REASON)

    wanted = wire_request.wanted.spend
    headroom = ledger.spend.headroom(goal_id, await _goal_spend_cap(deps, goal_id))
    outcome = decide_forage_request(
        ForageRequestSignal(within_headroom=wanted <= headroom, shrinkable=False)
    )
    if outcome is not ForageAutopilotOutcome.GRANT:
        return ForageRequestOutcome(outcome, None, _reason(outcome, "spend", wanted, headroom))
    revised = existing.model_copy(
        update={
            "spend_budget": existing.spend_budget + wanted,
            "revision": existing.revision + 1,
        }
    )
    await grants.revise(ledger, revised)
    return ForageRequestOutcome(
        ForageAutopilotOutcome.GRANT,
        revised,
        f"${wanted:.2f} more spend for goal {goal_id} granted from headroom (${headroom:.2f} "
        "free before this).",
    )


async def grant_wanted(
    ledger: ForageLedger, existing: ForageGrant, wire_request: WireForageRequest
) -> ForageGrant:
    """Grow `existing` by `wire_request.wanted`, in the request's own dimension, and commit it.

    Unconditional: no headroom check. Roadmap step 4.7's own leftover: `hivemind.queen.ticks.
    forage` calls this to actually grant a contested request once the Queen's awake episode has
    decided `GRANT_BY_SHRINKING` and another live grant has already been shrunk to free the
    headroom this call draws on -- the same per-dimension growth shape
    `handle_forage_request_for_kind`'s own three private handlers already apply after their own
    headroom check passes.

    Args:
        ledger: The Queen's live book.
        existing: The grant to grow; must already be known to the ledger.
        wire_request: The Warden's own request; `wire_request.kind` selects which dimension grows.

    Returns:
        The revised grant, already committed to the ledger.

    Raises:
        ValueError: `wire_request.kind` is BINDING (no ledger-side growth exists for it), or
            SHARED_SEATS with no `source_id` named.
    """
    revised = _grown_grant(existing, wire_request)
    await grants.revise(ledger, revised)
    return revised


def _grown_grant(existing: ForageGrant, wire_request: WireForageRequest) -> ForageGrant:
    """Return `existing` grown by `wire_request.wanted`, in the request's own dimension (pure)."""
    update: dict[str, object]
    if wire_request.kind is WireForageRequestKind.SUB_BEES:
        update = {"max_sub_bees": existing.max_sub_bees + wire_request.wanted.sub_bees}
    elif wire_request.kind is WireForageRequestKind.SHARED_SEATS:
        source_id = wire_request.wanted.source_id
        if source_id is None:
            raise ValueError(_SEATS_NEED_SOURCE_REASON)
        seats = _grow_seat_reservation(existing.seats, source_id, wire_request.wanted.seats)
        update = {"seats": seats}
    elif wire_request.kind is WireForageRequestKind.SPEND:
        update = {"spend_budget": existing.spend_budget + wire_request.wanted.spend}
    else:
        raise ValueError(_BINDING_OUT_OF_SCOPE_REASON)
    return existing.model_copy(update={**update, "revision": existing.revision + 1})


def _grow_seat_reservation(
    seats: tuple[SeatReservation, ...], source_id: str, wanted: int
) -> tuple[SeatReservation, ...]:
    """Return `seats` with `source_id`'s own reservation grown by `wanted`, adding one if absent."""
    for reservation in seats:
        if reservation.source_id == source_id:
            grown = reservation.model_copy(update={"seats": reservation.seats + wanted})
            return tuple(grown if s.source_id == source_id else s for s in seats)
    return (*seats, SeatReservation(source_id=source_id, seats=wanted))


def _shrinkable(other: int, wanted: int, free: int) -> bool:
    """Return whether `free` plus every other live grant's own committed amount covers `wanted`.

    A coarse over-approximation deliberately: this only decides whether the request is worth an
    awake episode's judgement (`hivemind.queen.autopilot.ForageAutopilotOutcome.NEEDS_JUDGEMENT`),
    never which grant to actually shrink -- that policy call is exactly what queen.awake does not
    yet support (this dispatch's own report names the gap).
    """
    return (free + other) >= wanted


def _reason(outcome: ForageAutopilotOutcome, dimension: str, wanted: float, free: float) -> str:
    """Explain a DENY or NEEDS_JUDGEMENT verdict, for the trail and the wire ForageReply."""
    if outcome is ForageAutopilotOutcome.NEEDS_JUDGEMENT:
        return (
            f"Wants {wanted} {dimension} against {free} free; cannot be met from headroom alone "
            "but shrinking another live grant could -- contested, needs judgement."
        )
    return f"Wants {wanted} {dimension} against {free} free, and no other live grant to shrink."


async def _goal_spend_cap(deps: QueenDeps, goal_id: TaskId) -> float:
    """Return the spend cap for `goal_id`'s goal: its request's budget when lower (step 10.5)."""
    try:
        task = await deps.chamber.get(goal_id)
    except TaskNotFoundError:
        return deps.budgets.spend_cap_usd  # No such task: the manifest's cap, as before 10.5.
    return goal_spend_cap(deps.budgets, task.spec)
