"""Clear the taint label: clear_taint, the one path by which a tainted item may be used again.

Roadmap step 10.6d (ADR-0043): "Only a judge verdict on the taint rubric, with no shared context,
clears it." `clear_taint` is that path, and the `taint_clear` enforcement point (ADR-0039) guards
it. It reads the item from its ledger and refuses outright unless it is TAINTED; asks the Guard's
`Enforcer` whether the clearer (the Queen, or a Warden for its own sub-bee's respawn) may clear an
item at that clearance, since clearing sends the item's whole text to the judge (the clearer must
hold `honey:clearance:<c>` for it; a refusal is a `guard.denied` row and no model call); refuses to
review an item too long to show the judge whole, rather than letting a verdict on its head clear
its tail; then asks the judge (`TaintJudge`, the JUDGE slot, no shared context) under a bounded
wait. Only a CLEAR verdict changes anything: the CLEARED marker and its `memory.taint_cleared`
event are written in one transaction. KEEP, a judge that cannot answer, or a judge that runs out
of time all leave the item exactly as it was (fail closed).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory.taint`. Called
    by whoever wants a tainted item back: the respawn path after a quarantine (roadmap 10.6c, "the
    only way out is a respawn from a Handoff the judge has cleared") and the Queen. Calls into
    `hivemind.cell`, `hivemind.guard` (Capability, Enforcer, EnforcementPoint and the policy
    request models), `hivemind.memory.context`, `hivemind.memory.errors`, `hivemind.pheromone`
    and this package's `judge`, `ledger`, `marker` and `state`.

Key invariants:
    - Nothing is written unless the judge returned CLEAR and the `taint_clear` point allowed it.
    - The judge sees only the `TaintReview` (the item's kind, setter, reason and whole text).
    - The `memory.taint_cleared` event names the item, the rubric and the event that tainted it;
      never the item's content or the judge's reasons (those are returned to the caller).

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the point.
    - hivemind.memory.taint.set for taint_memory, the one setter.
    - hivemind.memory.taint.judge for ModelTaintJudge and the rubric.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    EnforcementPoint,
    Enforcer,
    PolicyRequest,
    PrincipalRef,
)
from hivemind.memory.context import MemoryContext
from hivemind.memory.errors import InvalidTaintTransitionError, TaintJudgeError
from hivemind.memory.taint.judge import (
    MAX_REVIEW_CHARS,
    TaintJudge,
    TaintJudgement,
    TaintReview,
    TaintVerdict,
)
from hivemind.memory.taint.ledger import TaintableItem, TaintLedger
from hivemind.memory.taint.marker import TaintMarker, TaintState, TaintTarget
from hivemind.pheromone import MemoryEvent
from waggle.ids import new_event_id

TAINT_CLEARED_KIND = "memory.taint_cleared"  # The trail kind a clearing records.
# The longest one review may take, ladder retries and fallbacks included: past this the item stays
# tainted rather than holding its caller (a respawn, the Queen's tick) indefinitely.
TAINT_REVIEW_TIMEOUT_S = 300.0

__all__ = [
    "TAINT_CLEARED_KIND",
    "TAINT_REVIEW_TIMEOUT_S",
    "ClearOutcome",
    "TaintClearDeps",
    "TaintClearRequest",
    "TaintClearResult",
    "clear_taint",
]


class ClearOutcome(Enum):
    """How one clearing attempt ended."""

    CLEARED = "cleared"  # The judge returned CLEAR; the item is usable again.
    KEPT = "kept"  # The judge returned KEEP; nothing changed.
    REFUSED = "refused"  # The taint_clear point refused the clearer; no judge was asked.
    UNREVIEWABLE = "unreviewable"  # Too long to show whole, or the judge could not answer.


@dataclass(frozen=True, slots=True)
class TaintClearRequest:
    """Which item to clear, and who is asking (checked at the `taint_clear` point)."""

    target: TaintTarget  # The tainted item.
    clearer: PrincipalRef  # Who orders the review: the Queen, or a Warden for its own sub-bee.
    held: CapabilitySet  # What the clearer holds; it needs honey:clearance at the item's level.


@dataclass(frozen=True, slots=True)
class TaintClearDeps:
    """What a clearing needs: the judge, the Guard, and the memory tables to write to."""

    judge: TaintJudge  # The independent reviewer (ModelTaintJudge on ModelSlot.JUDGE).
    enforcer: Enforcer  # Checks the taint_clear point and records a refusal.
    ctx: MemoryContext  # The identity and clock events are stamped with; its store is the ledger.
    ledger: TaintLedger | None = None  # Where the item lives when not the memory tables (phase 7).


class TaintClearResult(BaseModel):
    """What one `clear_taint` call decided, with the judge's reasons when there was a verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: ClearOutcome = Field(description="How the attempt ended.")
    reasons: tuple[str, ...] = Field(default=(), description="The verdict's or refusal's why.")
    marker: TaintMarker | None = Field(
        default=None, description="The CLEARED marker now on the item; set only when CLEARED."
    )


async def clear_taint(request: TaintClearRequest, deps: TaintClearDeps) -> TaintClearResult:
    """Ask the judge whether a tainted item may be used again, and clear it only on CLEAR.

    Args:
        request: The item, the clearer and what the clearer holds.
        deps: The judge, the Enforcer and the memory tables (or the item's own ledger).

    Returns:
        CLEARED with the new marker, or KEPT, REFUSED or UNREVIEWABLE with nothing changed.

    Raises:
        hivemind.memory.errors.TaintTargetNotFoundError: No such item.
        hivemind.memory.errors.InvalidTaintTransitionError: The item is not TAINTED.
    """
    ledger = deps.ledger if deps.ledger is not None else deps.ctx.store
    item = await ledger.read_taintable(request.target)
    marker = item.marker
    # Only a TAINTED label can be cleared: clearing an unlabelled or already-cleared item is a
    # caller's bug, refused before any Guard check or model call.
    if marker is None or marker.state is not TaintState.TAINTED:
        before = marker.state if marker is not None else None
        raise InvalidTaintTransitionError(
            before, TaintState.CLEARED, item_id=request.target.item_id
        )
    decision = await deps.enforcer.check(_clear_request(request, item))
    if not decision.allowed:
        return TaintClearResult(outcome=ClearOutcome.REFUSED, reasons=(decision.reason,))
    verdict = await _judge(deps.judge, item, marker)
    if verdict is None:
        reason = "the item could not be reviewed whole, or the judge could not answer"
        return TaintClearResult(outcome=ClearOutcome.UNREVIEWABLE, reasons=(reason,))
    if verdict.judgement is not TaintJudgement.CLEAR:
        return TaintClearResult(outcome=ClearOutcome.KEPT, reasons=verdict.reasons)
    cleared = await _write_cleared(ledger, item, marker, verdict, deps.ctx)
    return TaintClearResult(outcome=ClearOutcome.CLEARED, reasons=verdict.reasons, marker=cleared)


def _clear_request(request: TaintClearRequest, item: TaintableItem) -> PolicyRequest:
    """Build the `taint_clear` check: the clearer must hold the item's own clearance."""
    # Clearing sends the item's whole text to the judge, so the clearer must be cleared to read
    # it: honey:clearance at the item's level (an ordered family, so a c2 holder clears a c1).
    needed = Capability(family=CapabilityFamily.HONEY_CLEARANCE, scope=item.clearance.value.lower())
    return PolicyRequest(
        principal=request.clearer,
        point=EnforcementPoint.TAINT_CLEAR,
        needed=needed,
        held=request.held,
    )


async def _judge(
    judge: TaintJudge, item: TaintableItem, marker: TaintMarker
) -> TaintVerdict | None:
    """Ask the judge about `item`; None when it cannot be shown whole or no verdict came back."""
    if len(item.content) > MAX_REVIEW_CHARS:
        return None  # Never judged on a partial reading: its unseen tail could hold the injection.
    review = TaintReview(
        kind=item.target.kind, source=marker.source, reason=marker.reason, content=item.content
    )
    try:
        # External await: one judge review, seconds to minutes with the ladder's own retries;
        # past the bound the item simply stays tainted.
        async with asyncio.timeout(TAINT_REVIEW_TIMEOUT_S):
            return await judge.review(review)
    except (TaintJudgeError, TimeoutError):
        return None  # No verdict is not a CLEAR verdict: the item stays refused (fail closed).


async def _write_cleared(
    ledger: TaintLedger,
    item: TaintableItem,
    marker: TaintMarker,
    verdict: TaintVerdict,
    ctx: MemoryContext,
) -> TaintMarker:
    """Write the CLEARED marker and its `memory.taint_cleared` event in one transaction."""
    event = MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind=TAINT_CLEARED_KIND,
        subject_id=item.target.item_id,
        payload={
            "item_kind": item.target.kind.value,
            "rubric_id": verdict.rubric_id,
            "tainted_by": marker.event_id,
            "source": marker.source.value,
        },
    )
    cleared = TaintMarker(
        state=TaintState.CLEARED,
        source=marker.source,
        reason=marker.reason,
        event_id=marker.event_id,
        at=marker.at,
        cleared_event_id=event.id,
        cleared_at=event.at,
    )
    await ledger.write_taint(item.target, cleared, event)
    return cleared
