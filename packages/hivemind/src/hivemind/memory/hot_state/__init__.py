"""Assemble hot state into a token-budgeted Prompt: the working-context tier of memory.

Hot state (codingrules section 8.9) is "active goals, open tasks and Alarms, pending questions,
recent decisions with reasons... pins, short notes": everything an awake episode's prompt needs
that is not the triggering event itself. This package holds the flat summary models a caller (the
Queen, a Warden) fills in from its own stores (`summaries`, since `hivemind.memory` may not import
`hivemind.brood_chamber` or `hivemind.supervision` -- same layer, no ADR lists that edge) and the
pure packing algorithm that turns them, plus pins and notes, into a `Prompt` under a token budget
(`packing`). Splitting the two keeps each file under codingrules 5.1's size limit and matches 5.2:
one file is a family of value models and a Protocol, the other is the packing logic that reads them.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). `assemble` is called by queen.awake and
    wardens.awake (a later roadmap step); `HotStateSources` is implemented by each of them over
    their own stores. Calls into hivemind.cell, hivemind.forage, hivemind.llm, hivemind.memory.
    counter, hivemind.memory.notes and hivemind.memory.pins.

Key invariants:
    - assemble's result never carries a RETRIEVED section (memory v0 has no Honey Store lookup
      yet); its sections are keyed only PINS and/or HOT_STATE.
    - Every candidate item is filtered by the principal's clearance allowance before it is ever
      scored, rendered or counted (codingrules section 8.9).

See Also:
    - .claude/codingrules.md section 8.9 for the memory tiers and packing rules this package
      implements.
    - hivemind.memory.hot_state.packing for assemble, AssembleRequest and Prompt.
    - hivemind.memory.hot_state.summaries for the summary models and HotStateSources.

Public API:
    - Principal, TokenBudget, TriggerEvent: assemble's inputs about the reader and the trigger.
    - TaskSummary, AlarmSummary, QuestionSummary, DecisionSummary: the flat hot-state candidates.
    - HotStateSources: the Protocol a caller implements to supply every candidate.
    - AssembleRequest, Prompt, assemble: the packing entry point and its request/result shapes.
    - ITEM_CAP_CHARS, RECENT_DECISIONS_LIMIT: packing's own tunable constants.
"""

from hivemind.memory.hot_state.packing import (
    ITEM_CAP_CHARS,
    RECENT_DECISIONS_LIMIT,
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
    DecisionSummary,
    HotStateSources,
    Principal,
    QuestionSummary,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
)

__all__ = [
    "ITEM_CAP_CHARS",
    "MAX_SUMMARY_OPTIONS",
    "RECENT_DECISIONS_LIMIT",
    "SUMMARY_OPTION_CAP_CHARS",
    "SUMMARY_TEXT_CAP_CHARS",
    "SUMMARY_TITLE_CAP_CHARS",
    "AlarmSummary",
    "AssembleRequest",
    "DecisionSummary",
    "HotStateSources",
    "Principal",
    "Prompt",
    "QuestionSummary",
    "TaskSummary",
    "TokenBudget",
    "TriggerEvent",
    "assemble",
]
