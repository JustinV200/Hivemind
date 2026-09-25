"""Handle one CellWaxProposed: autopilot within the per-Cell cap, else an awake decision.

Roadmap step 4.2a's own dispatch map: "Any bee, Warden or the human may propose one... a proposal
is an inbox item the Attendant scores low. Only the Queen writes, rejects or clears: autopilot
accepts a Warden's `NOTE` or `CAUTION` about its own Cell within the per-Cell cap, and every
`BLOCK`, every proposal from a Worker or about another Cell, and every clear is an awake decision."
`handle_wax_item` is the entry point `hivemind.queen.ticks.liveness.handle_infrastructure_item`
reaches for ahead of `hivemind.queen.autopilot.table.decide`, exactly the way that module already
reaches for a `waggle.messages.forage.ForageRequest` -- `decide`'s own fallback for an unrecognised
payload is `NEEDS_JUDGEMENT` outright, which would turn every routine, within-cap proposal into an
awake episode. `handle_wax_proposed` is the shared core, built to also accept a proposal that never
arrived over Waggle (roadmap step 4.2a: "or from the chat"), so `hivemind.queen.human_inbox.
propose_wax_from_chat`'s own built message reaches the exact same rule, not a parallel one.

Every proposal is recorded first (`hivemind.memory.cell_wax.propose_wax`, its own `memory.
wax_proposed` event), then judged by `hivemind.queen.autopilot.wax.decide_wax_proposal`: within the
cap, `write_wax` runs at once, with `decided_by=WaxDecision.AUTOPILOT` and no awake episode;
otherwise `hivemind.queen.awake.episode.decide_awake` runs with `cells_in_play = {proposed.cell_id}`
so the Cell's own existing wax is visible to the model, and its `QueenDecision.action` resolves the
proposal (`WRITE_WAX`/`REJECT_WAX`; anything else, including `CLEAR_WAX` -- never applicable to a
fresh proposal -- falls back to a rejection, never a silent write). Either way, a `CellWaxWritten`
goes back to the proposing Warden's own link when the note is written, so it holds the note even
when offline (`waggle.messages.cell.wax.CellWaxWritten`'s own docstring).

A proposal about a Night Veil Cell is refused before anything is recorded (codingrules section
12): the Cell is torn down once its task ends, and every record of it goes with it, while Cell Wax
is built to outlive the Cell it is about (and its `memory.wax_proposed` would carry the proposal's
own words onto the durable trail). The refusal itself is a `queen.decided` about the Cell, which
the Night Veil boundary keeps in the Cell's own segment and purges with it.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's ticks
    sub-package. Called by `hivemind.queen.ticks.liveness.handle_infrastructure_item` for every
    received `CellWaxProposed`, and directly by a future chat-facing caller for a human proposal.
    Calls into `hivemind.cell` (HoneyClearance), `hivemind.common.logging`, `hivemind.forage.slots`
    (Effort), `hivemind.hive.night_veil` (is_night_veil_cell), `hivemind.memory`
    (MemoryContext, TriggerEvent), `hivemind.memory.cell_wax` (CellWax, WaxProposalInput,
    WaxSeverity, WaxState, propose_wax, reject_wax, write_wax), `hivemind.queen.autopilot.wax`
    (WaxAutopilotOutcome, WaxProposalSignal, decide_wax_proposal), `hivemind.queen.awake`
    (QueenSources, decide_awake), `hivemind.queen.awake.decision` (QueenAction), `hivemind.queen.
    deps` (QueenDeps, WardenLink), `hivemind.queen.human_inbox` (HumanInbox), `hivemind.queen.
    trail` (record_event), `waggle.envelope`,
    `waggle.ids` and `waggle.messages.cell` (CellWaxProposed, CellWaxWritten) only.

Key invariants:
    - Every branch either writes or rejects the proposal exactly once; nothing here ever leaves a
      note PROPOSED past this function returning. A Night Veil Cell's proposal is never recorded.
    - `write_wax`/`reject_wax` (through `hivemind.memory.cell_wax`) already commit their own
      `memory.wax_*` event in the same store call as the row (codingrules section 12); this module
      adds no second event for the same edge.
    - A `CellWaxWritten` is sent only when the proposing Warden is still attached (`wardens.get`
      returns a link); an unreachable Warden still gets its note written and durably recorded, it
      only misses the immediate wire push (mirrors `hivemind.queen.ticks.forage.
      handle_forage_request_for_item`'s own "no link, nothing to do" shape).
    - Autopilot's per-Cell cap and text cap mirror `hivemind.manifest.schema.supervision.
      MemorySection`'s own `DEFAULT_CELL_WAX_CAP`/`wax_text_cap_chars` defaults exactly, since
      `QueenDeps` carries no manifest slice for either yet (flagged in this dispatch's own report).

See Also:
    - .claude/roadmap.md step 4.2a for the dispatch map this module implements.
    - .claude/codingrules.md Appendix C, "Cell Wax note" row, for the transition table
      `hivemind.memory.cell_wax.writes` walks on this module's behalf.
    - hivemind.queen.autopilot.wax for decide_wax_proposal, the pure rule this module applies.
    - hivemind.queen.awake.episode for decide_awake and QueenSources, the awake half.
    - hivemind.queen.ticks.forage for handle_forage_request_for_item, the sibling this module's
      own shape (and its "no link, nothing to do" rule) is modelled on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from hivemind.cell import HoneyClearance
from hivemind.common.logging import get_logger
from hivemind.forage.slots import Effort
from hivemind.hive.night_veil import is_night_veil_cell
from hivemind.memory import MemoryContext, TriggerEvent
from hivemind.memory.cell_wax import (
    MAX_WAX_TEXT_CHARS,
    CellWax,
    WaxProposalInput,
    WaxSeverity,
    WaxState,
    propose_wax,
    reject_wax,
    write_wax,
)
from hivemind.queen.autopilot import QueenAction
from hivemind.queen.autopilot.wax import WaxAutopilotOutcome, WaxProposalSignal, decide_wax_proposal
from hivemind.queen.awake import QueenSources, decide_awake
from hivemind.queen.awake.episode import WAX_CAP_PER_CELL, EpisodeExtras
from hivemind.queen.deps import QueenDeps, WardenLink
from hivemind.queen.human_inbox import HumanInbox
from hivemind.queen.trail import record_event
from hivemind.supervision.attendant import InboxItem
from waggle.envelope import wrap
from waggle.ids import WardenId
from waggle.messages.cell import CellWaxProposed, CellWaxWritten
from waggle.messages.cell.wax import WaxDecision
from waggle.messages.cell.wax import WaxSeverity as WireWaxSeverity

_JUDGEMENT_EFFORT = Effort.MEDIUM  # A Cell Wax judgement is rarer and weightier than routine
# traffic (never a Heartbeat), but is not the Queen's last chance before the human the way an
# escalated Alarm is; mirrors hivemind.queen.ticks.forage's own direct Effort choice for its
# contested case rather than routing through hivemind.queen.autopilot.effort.effort_for, since
# CellWaxProposed is not its own InboxKind (hivemind.queen.inbox.weights).
_AUTOPILOT_REASON = "A Warden's own NOTE or CAUTION about its own Cell, within the per-Cell cap."
_FALLBACK_REJECT_REASON = "The awake episode did not resolve to a write; rejected, not left open."
_NIGHT_VEIL_REFUSED = "night_veil_wax_refused"  # queen.decided's reason: wax outlives its Cell.

log = get_logger(__name__)

__all__ = ["handle_wax_item", "handle_wax_proposed"]


@dataclass(frozen=True, slots=True)
class _WrittenNotice:
    """The three fields `_send_written` needs about a just-written note, kept within 5.1's limit."""

    written: CellWax
    decided_by: WaxDecision
    reason: str


async def handle_wax_item(
    deps: QueenDeps, wardens: Mapping[WardenId, WardenLink], item: InboxItem
) -> bool:
    """Handle a CellWaxProposed InboxItem directly; report whether it was one.

    `hivemind.queen.ticks.liveness.handle_infrastructure_item` calls this ahead of `hivemind.
    queen.autopilot.table.decide`, whose own `NEEDS_JUDGEMENT` fallback is not what "within the
    per-Cell cap, no awake episode" (roadmap step 4.2a) means.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id.
        item: The ordered InboxItem to check; `item.principal` is the relaying Warden's own id.

    Returns:
        True if `item.payload` was a CellWaxProposed (handled either way); False otherwise.
    """
    if not isinstance(item.payload, CellWaxProposed):
        return False
    await handle_wax_proposed(deps, wardens, WardenId(item.principal), item.payload)
    return True


async def handle_wax_proposed(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    principal: WardenId,
    proposed: CellWaxProposed,
) -> None:
    """Record, then write or judge, one Cell Wax proposal.

    Args:
        deps: The Queen's collaborators.
        wardens: Every Warden currently attached, keyed by id.
        principal: The Warden this proposal arrived through; for a human proposal built directly
            (`hivemind.queen.human_inbox.propose_wax_from_chat`) rather than relayed by a Warden,
            the caller passes the Cell's own Warden so the "own Cell" check still resolves.
        proposed: The proposal itself.
    """
    if await is_night_veil_cell(deps.trail, proposed.cell_id):
        # Before propose_wax: its own record would carry the words past the Cell (module doc).
        await record_event(deps, "queen.decided", proposed.cell_id, reason=_NIGHT_VEIL_REFUSED)
        log.info("queen.wax_refused", reason=_NIGHT_VEIL_REFUSED)  # No id: logs outlive it too.
        return
    ctx = MemoryContext(store=deps.memory, identity=deps.identity, clock=deps.clock)
    wax = await propose_wax(_to_input(proposed), MAX_WAX_TEXT_CHARS, ctx)

    outcome = await _decide(deps, wardens, principal, proposed, wax)
    if outcome is WaxAutopilotOutcome.AUTOPILOT_WRITE:
        written = await write_wax(wax, WaxDecision.AUTOPILOT, _AUTOPILOT_REASON, ctx)
        notice = _WrittenNotice(written, WaxDecision.AUTOPILOT, _AUTOPILOT_REASON)
        await _send_written(deps, wardens, principal, notice)
        return
    await _judge(deps, wardens, principal, wax, ctx)


async def _decide(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    principal: WardenId,
    proposed: CellWaxProposed,
    wax: CellWax,
) -> WaxAutopilotOutcome:
    """Build the autopilot signal and return its verdict."""
    link = wardens.get(principal)
    own_cell = (
        link is not None and proposed.proposer == principal and link.cell.id == proposed.cell_id
    )
    written = await deps.memory.list_wax(
        proposed.cell_id, frozenset({WaxState.WRITTEN}), HoneyClearance.C2
    )
    signal = WaxProposalSignal(
        severity=proposed.severity,
        proposer_is_warden_about_own_cell=own_cell,
        written_count_for_cell=len(written),
        cap=WAX_CAP_PER_CELL,
    )
    return decide_wax_proposal(signal)


async def _judge(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    principal: WardenId,
    wax: CellWax,
    ctx: MemoryContext,
) -> None:
    """Run one awake episode on `wax` and act on its decision: write or reject."""
    sources = QueenSources(deps.chamber, deps.memory, HumanInbox())
    event = TriggerEvent(
        kind="cell.wax_proposed",
        summary=(
            f"Cell Wax proposed for {wax.cell_id}: {wax.severity.value} -- {wax.text} "
            f"(reason: {wax.reason})"
        ),
        payload_ref=wax.id,
        clearance=wax.clearance,
    )
    decision = await decide_awake(
        deps,
        event,
        sources,
        _JUDGEMENT_EFFORT,
        EpisodeExtras(cells_in_play=frozenset({wax.cell_id})),
    )
    if decision.action is QueenAction.WRITE_WAX:
        written = await write_wax(wax, WaxDecision.AWAKE, decision.reason, ctx)
        notice = _WrittenNotice(written, WaxDecision.AWAKE, decision.reason)
        await _send_written(deps, wardens, principal, notice)
        return
    reason = (
        decision.reason if decision.action is QueenAction.REJECT_WAX else _FALLBACK_REJECT_REASON
    )
    await reject_wax(wax, reason, ctx)


async def _send_written(
    deps: QueenDeps,
    wardens: Mapping[WardenId, WardenLink],
    principal: WardenId,
    notice: _WrittenNotice,
) -> None:
    """Send the written note back to the proposing Warden's own link, when it is still attached."""
    link = wardens.get(principal)
    if link is None:
        return  # Unreachable: the note is still written and durable (module docstring).
    written = notice.written
    message = CellWaxWritten(
        wax_id=written.id,
        cell_id=written.cell_id,
        severity=WireWaxSeverity(written.severity.value),
        text=written.text,
        reason=notice.reason,
        clearance=written.clearance.to_wire(),
        expires_at=written.expires_at,
        origin=written.origin,
        proposer=written.proposer,
        decided_by=notice.decided_by,
    )
    # The note is already written and durable (module docstring); an unreachable Warden only
    # misses the immediate wire push, the same "no link, nothing more to do" rule this module's
    # own docstring already states for a Warden that detached before this point.
    await link.send(wrap(message, link.hop, clock=deps.clock))


def _to_input(proposed: CellWaxProposed) -> WaxProposalInput:
    """Convert one wire CellWaxProposed into the WaxProposalInput propose_wax takes."""
    return WaxProposalInput(
        cell_id=proposed.cell_id,
        severity=WaxSeverity(proposed.severity.value),
        text=proposed.text,
        reason=proposed.reason,
        clearance=HoneyClearance.from_wire(proposed.clearance),
        origin=proposed.origin,
        proposer=proposed.proposer,
        task_id=proposed.task_id,
        expires_at=proposed.expires_at,
    )
