"""Define WaxAutopilotOutcome, WaxProposalSignal and decide_wax_proposal.

Roadmap step 4.2a: "autopilot accepts a Warden's `NOTE` or `CAUTION` about its own Cell within the
per-Cell cap, and every `BLOCK`, every proposal from a Worker or about another Cell, and every clear
is an awake decision." This is that one rule, built exactly like `hivemind.queen.autopilot.forage.
decide_forage_request`: a deterministic table over one already-computed signal, so it stays pure and
importable from an `autopilot/` directory (codingrules section 8.8: "a deterministic dispatch table
over (event kind, state)... before any model is consulted"). `WaxProposalSignal` groups the three
booleans and one count this table reads -- whether the proposer is the Warden that owns the Cell the
proposal is about, the proposed severity, and how many WRITTEN notes that Cell already has against
the manifest's own per-Cell cap -- all computed by the caller (`hivemind.queen.ticks.wax`, outside
this `autopilot/` directory) from the wire message and the store, since this module may touch no I/O
and no state of its own.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    autopilot sub-package (which never imports `hivemind.llm`). Called by `hivemind.queen.ticks.
    wax` once per received `waggle.messages.cell.CellWaxProposed`. Calls into
    `waggle.messages.cell.wax` (WaxSeverity) only.

Key invariants:
    - This module imports nothing beyond `dataclasses`, `enum` and `waggle.messages.cell.wax`:
      unlike `hivemind.memory.cell_wax` (whose own package `__init__` transitively reaches
      `hivemind.llm` through `hivemind.memory.hot_state.packing`'s `SectionLabel`), the wire
      `WaxSeverity` this module reads carries no such chain, so `decide_wax_proposal` reads it
      directly rather than the mirrored domain enum -- the two compare equal by value
      (`hivemind.memory.cell_wax.model`'s own docstring: mirrored member for member) and this
      module can never import `hivemind.llm`, directly or transitively (codingrules section 4;
      `lint-imports` enforces it for every module under an `autopilot/` directory).
    - `decide_wax_proposal` is pure: the same `WaxProposalSignal` always returns the same
      `WaxAutopilotOutcome`.
    - `WaxSeverity.BLOCK` never reaches `AUTOPILOT_WRITE`, regardless of the other three fields:
      "every BLOCK... is an awake decision" (roadmap step 4.2a) is a hard rule, not a preference.

See Also:
    - .claude/codingrules.md section 8.8 for the autopilot-first-awake-second shape this table
      implements.
    - .claude/roadmap.md step 4.2a for the autopilot rule this table is, verbatim.
    - hivemind.queen.autopilot.forage for decide_forage_request, the sibling rule this mirrors.
    - hivemind.queen.ticks.wax for handle_wax_proposed, the one caller that builds a
      WaxProposalSignal and acts on this table's own outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from waggle.messages.cell.wax import WaxSeverity

__all__ = ["WaxAutopilotOutcome", "WaxProposalSignal", "decide_wax_proposal"]


class WaxAutopilotOutcome(Enum):
    """What autopilot decides about one CellWaxProposed, before any awake episode runs."""

    AUTOPILOT_WRITE = "AUTOPILOT_WRITE"  # A Warden's own NOTE/CAUTION, within the per-Cell cap.
    NEEDS_JUDGEMENT = "NEEDS_JUDGEMENT"  # Every BLOCK, every other proposer, or over the cap.


@dataclass(frozen=True, slots=True)
class WaxProposalSignal:
    """The three fields `decide_wax_proposal` reads, precomputed by the caller.

    Attributes:
        severity: The proposed note's own severity.
        proposer_is_warden_about_own_cell: True when the proposer is a Warden and the Cell named
            is that same Warden's own Cell; false for a Worker's proposal, a proposal about
            another Cell, or a human proposal (`origin is HUMAN`, `proposer is None`).
        written_count_for_cell: How many WRITTEN notes the proposed Cell already has.
        cap: The manifest's own per-Cell cap (`[memory] cell_wax_cap`).
    """

    severity: WaxSeverity
    proposer_is_warden_about_own_cell: bool
    written_count_for_cell: int
    cap: int


def decide_wax_proposal(signal: WaxProposalSignal) -> WaxAutopilotOutcome:
    """Return autopilot's verdict on one CellWaxProposed, from an already-computed signal.

    Args:
        signal: Whether the proposer is the Cell's own Warden, the proposed severity, and how the
            Cell's current WRITTEN count compares to the manifest's own cap.

    Returns:
        AUTOPILOT_WRITE only for a Warden's NOTE or CAUTION about its own Cell, strictly within
        the cap; NEEDS_JUDGEMENT for everything else (every BLOCK, a Worker's proposal, a
        proposal about another Cell, a human proposal, or one at or over the cap).
    """
    if not signal.proposer_is_warden_about_own_cell:
        return WaxAutopilotOutcome.NEEDS_JUDGEMENT
    if signal.severity is WaxSeverity.BLOCK:
        return WaxAutopilotOutcome.NEEDS_JUDGEMENT
    if signal.written_count_for_cell >= signal.cap:
        return WaxAutopilotOutcome.NEEDS_JUDGEMENT
    return WaxAutopilotOutcome.AUTOPILOT_WRITE
