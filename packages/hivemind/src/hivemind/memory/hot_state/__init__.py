"""Assemble hot state into a token-budgeted Prompt: the working-context tier of memory.

Hot state (codingrules section 8.9) is "active goals, open tasks and Alarms, pending questions,
recent decisions with reasons... pins, short notes": everything an awake episode's prompt needs
that is not the triggering event itself. This package holds the flat summary models a caller (the
Queen, a Warden) fills in from its own stores (`summaries`, since `hivemind.memory` may not import
`hivemind.brood_chamber` or `hivemind.supervision` -- same layer, no ADR lists that edge) and the
pure packing algorithm that turns them, plus pins and notes, into a `Prompt` under a token budget
(`packing`). Splitting the two keeps each file under codingrules 5.1's size limit and matches 5.2:
one file is a family of value models and a Protocol, the other is the packing logic that reads them.
`untrusted` (roadmap step 10.6b) renders outside text under the untrusted-content scanner's
verdict, fenced as data, labelled harder or withheld, for `assemble` and for a Worker's tool
results alike, and holds `RetrievedItem`, the phase 7 seam for Honey hits and Nectar at assembly.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `assemble` is called by queen.awake and
    wardens.awake (a later roadmap step); `HotStateSources` is implemented by each of them over
    their own stores. Calls into hivemind.cell, hivemind.forage, hivemind.llm, hivemind.memory.
    counter, hivemind.memory.notes, hivemind.memory.pins, hivemind.memory.taint (the label) and
    hivemind.guard.scanner (the verdicts rendered).

Key invariants:
    - assemble's result carries a RETRIEVED section only when the caller passed retrieved items
      (the phase 7 seam); until then its sections are keyed only PINS and/or HOT_STATE.
    - A TAINTED item (a decision, the resumed Handoff, a retrieved item) never reaches a Prompt,
      and outside text is always rendered under its scan verdict (roadmap steps 10.6b, 10.6d).
    - Every candidate item is filtered by the principal's clearance allowance before it is ever
      scored, rendered or counted (codingrules section 8.9).

See Also:
    - .claude/codingrules.md section 8.9 for the memory tiers and packing rules this package
      implements.
    - hivemind.memory.hot_state.packing for assemble, AssembleRequest and Prompt.
    - hivemind.memory.hot_state.summaries for the summary models and HotStateSources.

Public API:
    - Principal, TokenBudget, TriggerEvent: assemble's inputs about the reader and the trigger.
    - TaskSummary, AlarmSummary, QuestionSummary, DecisionSummary, CellWaxSummary: the flat
      hot-state candidates.
    - HotStateSources: the Protocol a caller implements to supply every candidate.
    - AssembleRequest, Prompt, assemble: the packing entry point and its request/result shapes.
    - ITEM_CAP_CHARS, RECENT_DECISIONS_LIMIT, RESUMED_HANDOFF_ID: packing's own constants.
    - UntrustedText, RetrievedItem, RetrievedKind, render_untrusted, render_retrieved: outside
      text under its scan verdict, and the phase 7 retrieval seam (untrusted).
"""

from hivemind.memory.hot_state.packing import (
    ITEM_CAP_CHARS,
    RECENT_DECISIONS_LIMIT,
    RESUMED_HANDOFF_ID,
    AssembleRequest,
    Prompt,
    assemble,
)
from hivemind.memory.hot_state.summaries import (
    MAX_SUMMARY_OPTIONS,
    SUMMARY_OPTION_CAP_CHARS,
    SUMMARY_TEXT_CAP_CHARS,
    SUMMARY_TITLE_CAP_CHARS,
    AlarmSummary,
    CellWaxSummary,
    DecisionSummary,
    HotStateSources,
    Principal,
    QuestionSummary,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
)
from hivemind.memory.hot_state.untrusted import (
    RetrievedItem,
    RetrievedKind,
    UntrustedText,
    render_retrieved,
    render_untrusted,
)

__all__ = [
    "ITEM_CAP_CHARS",
    "MAX_SUMMARY_OPTIONS",
    "RECENT_DECISIONS_LIMIT",
    "RESUMED_HANDOFF_ID",
    "SUMMARY_OPTION_CAP_CHARS",
    "SUMMARY_TEXT_CAP_CHARS",
    "SUMMARY_TITLE_CAP_CHARS",
    "AlarmSummary",
    "AssembleRequest",
    "CellWaxSummary",
    "DecisionSummary",
    "HotStateSources",
    "Principal",
    "Prompt",
    "QuestionSummary",
    "RetrievedItem",
    "RetrievedKind",
    "TaskSummary",
    "TokenBudget",
    "TriggerEvent",
    "UntrustedText",
    "assemble",
    "render_retrieved",
    "render_untrusted",
]
