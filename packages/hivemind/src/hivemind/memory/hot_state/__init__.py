"""Assemble hot state and retrieved Honey into a token-budgeted Prompt: the working-context tier.

Hot state (codingrules section 8.9) is "active goals, open tasks and Alarms, pending questions,
recent decisions with reasons... pins, short notes": everything an awake episode's prompt needs
that is not the triggering event itself. This package holds the flat summary models a caller (the
Queen, a Warden) fills in from its own stores (`summaries`, since `hivemind.memory` may not import
`hivemind.brood_chamber` or `hivemind.supervision` -- same layer, no ADR lists that edge), the
pure packing algorithm that turns them, plus pins and notes, into a `Prompt` under a token budget
(`packing`), and the cold tier's own step (`retrieved`): Honey hits (retrieved rows of the Hive's
ripened, labelled knowledge) the caller scanned and hands in as data, packed after hot state into
the `RETRIEVED` section within their own share of the budget. `untrusted` (roadmap step 10.6b)
renders outside text under the untrusted-content scanner's verdict, fenced as data, labelled harder
or withheld, for `assemble`, the retrieved items and a Worker's tool results alike, and holds
`RetrievedItem`, the phase 7 seam every retrieved hit crosses already scanned. Splitting the four
keeps each file under codingrules 5.1's size limit and matches 5.2: one file is a family of value
models and a Protocol, one the packing logic that reads them, one the rendering and packing of
retrieved items, one the rendering of outside text.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `assemble` is called by queen.awake,
    wardens.awake and the Worker roles (`hivemind.workers.roles.bounded_loop.prompt`);
    `HotStateSources` is implemented by each of them over their own stores. Calls into
    hivemind.cell, hivemind.forage, hivemind.llm, hivemind.memory.counter, hivemind.memory.notes,
    hivemind.memory.pins, hivemind.memory.taint (the label), hivemind.guard.scanner (the verdicts
    rendered) and waggle; never hivemind.honey_store, whose hits arrive as scanned `RetrievedItem`s.

Key invariants:
    - assemble's result carries a RETRIEVED section only when the caller passed retrieved items
      (`AssembleRequest.retrieved`) and at least one fit; its sections are keyed PINS, HOT_STATE
      and RETRIEVED, each present only when it has content.
    - Every candidate item and every retrieved item is filtered by the principal's clearance
      allowance before it is ever scored, rendered or counted (codingrules section 8.9).
    - A TAINTED item (a decision, the resumed Handoff, a retrieved item) never reaches a Prompt,
      and outside text is always rendered under its scan verdict (roadmap steps 10.6b, 10.6d).
    - Retrieved items never take room from hot state, and never more than
      `TokenBudget.retrieved_fraction` of the packing target.

See Also:
    - .claude/codingrules.md section 8.9 for the memory tiers and packing rules this package
      implements, and section 15 for why retrieved content is labelled as data.
    - hivemind.memory.hot_state.packing for assemble, AssembleRequest and Prompt.
    - hivemind.memory.hot_state.summaries for the summary models and HotStateSources.
    - hivemind.memory.hot_state.retrieved for how retrieved hits are rendered and packed.

Public API:
    - Principal, TokenBudget, TriggerEvent: assemble's inputs about the reader and the trigger.
    - TaskSummary, AlarmSummary, QuestionSummary, DecisionSummary, CellWaxSummary: the flat
      hot-state candidates.
    - HotStateSources: the Protocol a caller implements to supply every candidate.
    - AssembleRequest, Prompt, assemble: the packing entry point and its request/result shapes.
    - ITEM_CAP_CHARS, RECENT_DECISIONS_LIMIT, DEFAULT_RETRIEVED_FRACTION, RESUMED_HANDOFF_ID:
      packing's own constants.
    - RETRIEVED_PREAMBLE, render_hit, render_item: the retrieved section's opening line and one
      block, public so a tool result or a prompt carrying hits can show them in the same shape.
    - UntrustedText, RetrievedItem, RetrievedKind, HONEY_HIT_LABEL, render_untrusted, within_scan:
      outside text under its scan verdict, and the scanned item every retrieved hit becomes
      (untrusted).
"""

from hivemind.memory.hot_state.packing import (
    ITEM_CAP_CHARS,
    RECENT_DECISIONS_LIMIT,
    RESUMED_HANDOFF_ID,
    AssembleRequest,
    Prompt,
    assemble,
)
from hivemind.memory.hot_state.retrieved import RETRIEVED_PREAMBLE, render_hit, render_item
from hivemind.memory.hot_state.summaries import (
    DEFAULT_RETRIEVED_FRACTION,
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
    HONEY_HIT_LABEL,
    RetrievedItem,
    RetrievedKind,
    UntrustedText,
    render_untrusted,
    within_scan,
)

__all__ = [
    "DEFAULT_RETRIEVED_FRACTION",
    "HONEY_HIT_LABEL",
    "ITEM_CAP_CHARS",
    "MAX_SUMMARY_OPTIONS",
    "RECENT_DECISIONS_LIMIT",
    "RESUMED_HANDOFF_ID",
    "RETRIEVED_PREAMBLE",
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
    "render_hit",
    "render_item",
    "render_untrusted",
    "within_scan",
]
