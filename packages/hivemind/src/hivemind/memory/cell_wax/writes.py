"""Define propose_wax, write_wax, reject_wax, clear_wax, expire_wax and retire_wax_for_cell.

Roadmap step 4.2a: the transition table `PROPOSED -> WRITTEN -> CLEARED | EXPIRED`,
`PROPOSED -> REJECTED`, "each walking the table and committing its `memory.wax_*` event in the
same transaction through the store." Every function here does exactly that: check the edge with
`hivemind.memory.cell_wax.state.assert_transition`, build the next `CellWax` row with
`model_copy`, and hand both it and its `MemoryEvent` to `MemoryStore.put_wax`/`update_wax_state` in
one call (codingrules section 12). `propose_wax` is the one function that inserts a fresh row
(state `PROPOSED`) rather than transitioning an existing one; it is also where the manifest's own
`[memory] wax_text_cap_chars` is enforced (`hivemind.memory.cell_wax.model.CellWax.text`'s own
`Field` only bounds the wire shape's hard ceiling, `MAX_WAX_TEXT_CHARS` -- see that module's
docstring). `retire_wax_for_cell` is roadmap step 4.2a's own forward-looking hook: "leave a
`retire_wax_for_cell` function now" for phase 5, when a destroyed Virtual Cell retires its wax; it
is a `WRITTEN -> CLEARED` edge like any other clear, tagged `WaxClearCause.CELL_RETIRED` so the
history still shows why, and it is not called from anywhere in this dispatch (no Virtual Cell
lifecycle hook exists yet to call it from).

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside the `cell_wax` sub-package.
    Called by `hivemind.queen.ticks.wax` (the Queen's own dispatch for a proposal, and, once
    phase 7 lands ripening, for the House Bee sweep's own wax-expiry hook,
    `hivemind.workers.roles.house_bee.sweep`). Calls into `hivemind.memory.cell_wax.model`
    (CellWax, MAX_WAX_REASON_CHARS, new_wax_id), `hivemind.memory.cell_wax.state` (WaxState,
    assert_transition), `hivemind.memory.context` (MemoryContext), `hivemind.memory.errors`
    (WaxTextTooLongError), `hivemind.pheromone` (MemoryEvent), `waggle.messages.cell.wax`
    (WaxClearCause, WaxDecision) and waggle only.

Key invariants:
    - Every function here either raises (an illegal edge, or an oversized proposal) or returns a
      `CellWax` whose row and accompanying `memory.wax_*` event have both already committed
      together, through the store (codingrules section 12): there is no function here that leaves
      a caller holding a value the store has not yet durably recorded.
    - Only `propose_wax` ever mints a fresh id (`new_wax_id`); every other function carries the
      same `wax.id` forward unchanged across every transition.
    - `clear_wax`'s own `cause` defaults to `WaxClearCause.CLEARED` (the Queen's own clear); the
      one other legal cause for a `WRITTEN -> CLEARED` edge, `CELL_RETIRED`, only ever comes from
      `retire_wax_for_cell`.

See Also:
    - .claude/roadmap.md step 4.2a for this module's spec verbatim.
    - .claude/codingrules.md Appendix C, "Cell Wax note" row, for the transition table this module
      walks.
    - hivemind.memory.cell_wax.state for WaxState and assert_transition, the table this module
      never bypasses.
    - hivemind.memory.cell_wax.model for CellWax, the row every function here builds or returns.
    - hivemind.queen.ticks.wax for handle_wax_proposed, this module's primary caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from hivemind.cell import HoneyClearance
from hivemind.memory.cell_wax.model import MAX_WAX_REASON_CHARS, CellWax, WaxSeverity, new_wax_id
from hivemind.memory.cell_wax.state import WaxState, assert_transition
from hivemind.memory.context import MemoryContext
from hivemind.memory.errors import WaxTextTooLongError
from hivemind.pheromone import MemoryEvent
from waggle.ids import CellId, TaskId, new_event_id
from waggle.messages.cell.wax import WaxClearCause, WaxDecision, WaxOrigin

__all__ = [
    "WaxProposalInput",
    "clear_wax",
    "expire_wax",
    "propose_wax",
    "reject_wax",
    "retire_wax_for_cell",
    "write_wax",
]


@dataclass(frozen=True, slots=True)
class WaxProposalInput:
    """Everything `propose_wax` needs to mint a fresh, PROPOSED CellWax row.

    Attributes:
        cell_id: The Cell this note is about.
        severity: How much it should weigh, once WRITTEN.
        text: The caution itself; checked against the manifest's own cap before construction.
        reason: Why the proposer believes it.
        clearance: The note's own data-sensitivity label.
        origin: Who noticed it: a bee, a Patrol, or the human.
        proposer: The proposing bee's own id, as text; None exactly when origin is HUMAN.
        task_id: The task during which it was noticed, if any.
        expires_at: When it expires on its own; None for standing wax.
    """

    cell_id: CellId
    severity: WaxSeverity
    text: str
    reason: str
    clearance: HoneyClearance
    origin: WaxOrigin
    proposer: str | None = None
    task_id: TaskId | None = None
    expires_at: datetime | None = None


async def propose_wax(inputs: WaxProposalInput, text_cap_chars: int, ctx: MemoryContext) -> CellWax:
    """Insert a fresh, PROPOSED CellWax row and record its `memory.wax_proposed` event.

    Any bee, Warden or the human may propose one (roadmap step 4.2a); this function only records
    the proposal, it never decides whether it is accepted -- see `hivemind.queen.autopilot.wax`
    and `hivemind.queen.ticks.wax` for that.

    Args:
        inputs: The proposal's own content.
        text_cap_chars: The manifest's own `[memory] wax_text_cap_chars`, checked before
            `inputs.text` is ever handed to `CellWax` (whose own `Field` only bounds the wire
            shape's larger hard ceiling).
        ctx: The store, identity and clock to write with.

    Returns:
        The newly proposed CellWax, state PROPOSED.

    Raises:
        WaxTextTooLongError: `inputs.text` is longer than `text_cap_chars`.
    """
    if len(inputs.text) > text_cap_chars:
        raise WaxTextTooLongError(len(inputs.text), text_cap_chars)
    wax = CellWax(
        id=new_wax_id(ctx.clock),
        cell_id=inputs.cell_id,
        state=WaxState.PROPOSED,
        severity=inputs.severity,
        text=inputs.text,
        proposer=inputs.proposer,
        origin=inputs.origin,
        reason=inputs.reason[:MAX_WAX_REASON_CHARS],
        clearance=inputs.clearance,
        task_id=inputs.task_id,
        expires_at=inputs.expires_at,
        proposed_at=ctx.clock.now(),
    )
    event = _event(ctx, "memory.wax_proposed", wax, reason=wax.reason)
    await ctx.store.put_wax(wax, event)
    return wax


async def write_wax(
    wax: CellWax, decided_by: WaxDecision, decision_reason: str, ctx: MemoryContext
) -> CellWax:
    """Move `wax` PROPOSED -> WRITTEN and record its `memory.wax_written` event.

    Args:
        wax: The proposed note; must currently be PROPOSED.
        decided_by: Whether autopilot or an awake episode wrote it.
        decision_reason: Why the Queen wrote it, for the trail (never stored on the row itself:
            `CellWax.reason` stays the proposer's own reason, unchanged).
        ctx: The store, identity and clock to write with.

    Returns:
        The written CellWax, state WRITTEN.

    Raises:
        InvalidWaxTransitionError: `wax.state` is not PROPOSED.
    """
    assert_transition(wax.state, WaxState.WRITTEN, subject_id=wax.id)
    written = wax.model_copy(
        update={"state": WaxState.WRITTEN, "decided_by": decided_by, "decided_at": ctx.clock.now()}
    )
    event = _event(
        ctx, "memory.wax_written", written, decided_by=decided_by.value, reason=decision_reason
    )
    await ctx.store.update_wax_state(written, event)
    return written


async def reject_wax(wax: CellWax, decision_reason: str, ctx: MemoryContext) -> CellWax:
    """Move `wax` PROPOSED -> REJECTED and record its `memory.wax_rejected` event.

    Args:
        wax: The proposed note; must currently be PROPOSED.
        decision_reason: Why the Queen refused it, for the trail.
        ctx: The store, identity and clock to write with.

    Returns:
        The rejected CellWax, state REJECTED.

    Raises:
        InvalidWaxTransitionError: `wax.state` is not PROPOSED.
    """
    assert_transition(wax.state, WaxState.REJECTED, subject_id=wax.id)
    rejected = wax.model_copy(update={"state": WaxState.REJECTED, "decided_at": ctx.clock.now()})
    event = _event(ctx, "memory.wax_rejected", rejected, reason=decision_reason)
    await ctx.store.update_wax_state(rejected, event)
    return rejected


async def clear_wax(
    wax: CellWax,
    decision_reason: str,
    ctx: MemoryContext,
    cause: WaxClearCause = WaxClearCause.CLEARED,
) -> CellWax:
    """Move `wax` WRITTEN -> CLEARED and record its `memory.wax_cleared` event.

    Args:
        wax: The written note; must currently be WRITTEN.
        decision_reason: Why it left WRITTEN, for the trail.
        ctx: The store, identity and clock to write with.
        cause: `CLEARED` (the Queen's own clear) unless called from `retire_wax_for_cell`, which
            passes `CELL_RETIRED`.

    Returns:
        The cleared CellWax, state CLEARED.

    Raises:
        InvalidWaxTransitionError: `wax.state` is not WRITTEN.
    """
    assert_transition(wax.state, WaxState.CLEARED, subject_id=wax.id)
    cleared = wax.model_copy(
        update={"state": WaxState.CLEARED, "clear_cause": cause, "cleared_at": ctx.clock.now()}
    )
    event = _event(ctx, "memory.wax_cleared", cleared, cause=cause.value, reason=decision_reason)
    await ctx.store.update_wax_state(cleared, event)
    return cleared


async def expire_wax(wax: CellWax, ctx: MemoryContext) -> CellWax:
    """Move `wax` WRITTEN -> EXPIRED and record its `memory.wax_expired` event.

    Called by the House Bee sweep (`hivemind.workers.roles.house_bee.sweep`) for every WRITTEN
    note whose `expires_at` has passed.

    Args:
        wax: The written note; must currently be WRITTEN, with `expires_at` in the past.
        ctx: The store, identity and clock to write with.

    Returns:
        The expired CellWax, state EXPIRED.

    Raises:
        InvalidWaxTransitionError: `wax.state` is not WRITTEN.
    """
    assert_transition(wax.state, WaxState.EXPIRED, subject_id=wax.id)
    expired = wax.model_copy(
        update={
            "state": WaxState.EXPIRED,
            "clear_cause": WaxClearCause.EXPIRED,
            "cleared_at": ctx.clock.now(),
        }
    )
    event = _event(
        ctx, "memory.wax_expired", expired, reason="expires_at passed on a House Bee sweep"
    )
    await ctx.store.update_wax_state(expired, event)
    return expired


async def retire_wax_for_cell(cell_id: CellId, ctx: MemoryContext) -> tuple[CellWax, ...]:
    """Clear every WRITTEN note about `cell_id`, tagged CELL_RETIRED (phase 5's own hook).

    Not called from anywhere in this dispatch: a Real Cell's wax outlives its leases (nothing to
    retire), and a Virtual Cell's destroy path (`hivemind.hive.lifecycle`, phase 5) does not yet
    call it. Left here now, as roadmap step 4.2a asks, so that hook is a one-line addition later
    rather than a new function to design from scratch.

    Args:
        cell_id: The (Virtual) Cell being destroyed.
        ctx: The store, identity and clock to write with.

    Returns:
        Every note this call cleared, in the order `list_wax` returned them; empty if `cell_id`
        had no WRITTEN wax.
    """
    written = await ctx.store.list_wax(cell_id, frozenset({WaxState.WRITTEN}), HoneyClearance.C2)
    cleared: list[CellWax] = []
    for wax in written:
        cleared.append(
            await clear_wax(wax, "Its Cell was destroyed.", ctx, cause=WaxClearCause.CELL_RETIRED)
        )
    return tuple(cleared)


def _event(ctx: MemoryContext, kind: str, wax: CellWax, **payload: object) -> MemoryEvent:
    """Build the `memory.wax_*` trail event that accompanies one CellWax write, in one place.

    `subject_id` is `wax.cell_id`, not `wax.id`: `PheromoneEvent.subject_id` only accepts a
    well-formed id of a known `waggle.ids.IdKind`, and none exists for a Cell Wax note yet
    (`hivemind.memory.cell_wax.model`'s own module docstring); the Cell it marks always has one,
    and is what every one of these events is fundamentally about. The note's own id still travels,
    in `payload`, so a reader can still find the exact row.
    """
    return MemoryEvent(
        id=new_event_id(ctx.clock),
        hive_id=ctx.identity.hive_id,
        node_id=ctx.identity.node_id,
        at=ctx.clock.now(),
        actor=ctx.identity.actor,
        kind=kind,
        subject_id=wax.cell_id,
        payload={"wax_id": wax.id, "severity": wax.severity.value, **payload},
    )
