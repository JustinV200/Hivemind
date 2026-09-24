"""Taint: one label that keeps memory written under compromise out of every later prompt.

A bee that read an injected instruction may have written it into its memory: a Handoff it will
resume from, an episode record, a checkpoint's transcript, a Nectar deposit. Roadmap step 10.6d
(ADR-0035) labels such memory tainted, and a tainted item is refused outright by `memory.assemble`,
by retrieval and by every Handoff loader, whatever its relevance, until a judge verdict on the
taint rubric clears it. `marker` holds the one label (`TaintMarker`: its closed `TaintSource`, its
reason, the `memory.tainted` event that set it and when) and what it can sit on (`TaintedKind`,
`TaintTarget`); `state` is its transition table (unlabelled or CLEARED -> TAINTED -> CLEARED);
`scope` says which slice of memory one taint covers (`TaintScope.for_bee`, `.for_cell`); `ledger`
is the store seam (`TaintLedger`, which the memory tables satisfy and the Honey Store will in
phase 7); `set` is the one setter (`taint_memory`); `judge` and `clear` are the one clearer
(`ModelTaintJudge` on the JUDGE slot, `clear_taint` through the `taint_clear` enforcement point);
`seams` declares the House Bee's phase 7 re-ripen duty.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy), inside `hivemind.memory`. Set by the
    Queen's isolation (10.6a), the quarantine intervention (10.6c) and the Queen on a Guard report
    about a Honey item (phase 7); read by every memory reader. Calls into `hivemind.cell`,
    `hivemind.common.logging`, `hivemind.guard`, `hivemind.llm`, `hivemind.memory.context`,
    `hivemind.memory.errors`, `hivemind.pheromone` and waggle; never into the rest of
    `hivemind.memory`, so every memory model can carry the marker without an import cycle.

Key invariants:
    - Only `taint_memory` writes a TAINTED label and only `clear_taint` writes a CLEARED one, each
      with its trail event in the same transaction (a test walks the source tree to hold every
      module to that: tests/unit/memory/taint/test_only_setter.py).
    - A TAINTED item never reaches a prompt or a resumed bee.

See Also:
    - docs/adr/0035-guard-bee-requests-queen-only-isolation-and-tainted-memory.md.
    - docs/guard/tainted-memory.md for the label's life, the scopes and the seams.
    - .claude/codingrules.md Appendix C, "Taint label" row.

Public API:
    - TaintMarker, TaintSource, TaintState, TaintedKind, TaintTarget, is_refused,
      require_unlabelled, MAX_TAINT_REASON_CHARS: the label and what it sits on (marker).
    - TRANSITIONS, can_transition, assert_transition: the label's state machine (state).
    - TaintScope: which slice of memory one taint covers (scope).
    - TaintLedger, TaintableItem: the store seam (ledger).
    - taint_memory, TaintStamp, TaintReport, TAINTED_KIND: the one setter (set).
    - TaintJudge, ModelTaintJudge, TaintReview, TaintVerdict, TaintJudgement, TAINT_RUBRIC_ID,
      MAX_REVIEW_CHARS: the independent judge (judge).
    - clear_taint, TaintClearRequest, TaintClearDeps, TaintClearResult, ClearOutcome,
      TAINT_CLEARED_KIND, TAINT_REVIEW_TIMEOUT_S: the one clearer (clear).
    - TaintedNectarRipener: the House Bee's phase 7 re-ripen duty, declared only (seams).
"""

from hivemind.memory.taint.clear import (
    TAINT_CLEARED_KIND,
    TAINT_REVIEW_TIMEOUT_S,
    ClearOutcome,
    TaintClearDeps,
    TaintClearRequest,
    TaintClearResult,
    clear_taint,
)
from hivemind.memory.taint.judge import (
    MAX_REVIEW_CHARS,
    TAINT_RUBRIC_ID,
    ModelTaintJudge,
    TaintJudge,
    TaintJudgement,
    TaintReview,
    TaintVerdict,
)
from hivemind.memory.taint.ledger import TaintableItem, TaintLedger
from hivemind.memory.taint.marker import (
    MAX_TAINT_REASON_CHARS,
    TaintedKind,
    TaintMarker,
    TaintSource,
    TaintState,
    TaintTarget,
    is_refused,
    require_unlabelled,
)
from hivemind.memory.taint.scope import TaintScope
from hivemind.memory.taint.seams import TaintedNectarRipener
from hivemind.memory.taint.set import TAINTED_KIND, TaintReport, TaintStamp, taint_memory
from hivemind.memory.taint.state import TRANSITIONS, assert_transition, can_transition

__all__ = [
    "MAX_REVIEW_CHARS",
    "MAX_TAINT_REASON_CHARS",
    "TAINTED_KIND",
    "TAINT_CLEARED_KIND",
    "TAINT_REVIEW_TIMEOUT_S",
    "TAINT_RUBRIC_ID",
    "TRANSITIONS",
    "ClearOutcome",
    "ModelTaintJudge",
    "TaintClearDeps",
    "TaintClearRequest",
    "TaintClearResult",
    "TaintJudge",
    "TaintJudgement",
    "TaintLedger",
    "TaintMarker",
    "TaintReport",
    "TaintReview",
    "TaintScope",
    "TaintSource",
    "TaintStamp",
    "TaintState",
    "TaintTarget",
    "TaintVerdict",
    "TaintableItem",
    "TaintedKind",
    "TaintedNectarRipener",
    "assert_transition",
    "can_transition",
    "clear_taint",
    "is_refused",
    "require_unlabelled",
    "taint_memory",
]
