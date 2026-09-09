"""Memory v0: assemble hot state into a prompt, checkpoint a Handoff, pin facts, and note things.

Context is treated like a cache hierarchy (README "Core concept 7: Memory"): what any bee's model
sees is assembled fresh for each awake episode from durable state, never accumulated as a
conversation (codingrules section 8.8: "Awake episodes are stateless"). This package covers the
working and hot tiers of that hierarchy for roadmap step 3.14: `hot_state` packs active tasks, open
Alarms, pending questions, recent decisions, pins and notes into a token-budgeted `Prompt`
(`assemble`); `handoff` is the resumable snapshot a bee writes before its context resets
(`Handoff`); `checkpoint` writes and reads one (`write_checkpoint`/`read_handoff`); `pins` and
`notes` are the two ways a fact enters hot state without going through a whole episode (`Pin`,
`Note`); `episodes` records every awake episode's or autopilot decision's thinking, streamable live
to the Observation Hive (`EpisodeRecord`, `EpisodeStream`); `counter` estimates or counts tokens
before a call is made (`TokenCounter`); `context` bundles the collaborators every write shares
(`MemoryContext`); `store` is the durable half, four SQLite tables plus an in-memory fake for
tests. Bee terms used here: a **Handoff** is a structured document a bee writes to resume its own
work later (possibly on a different model or host); **hot state** is the always-loaded, bounded
slice of memory an episode's prompt is built from; a **Pin** is a fact that never decays out of
hot state; **Honey clearance** (`hivemind.cell.HoneyClearance`) is the data-sensitivity label every
row in this package carries.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.awake and wardens.awake
    (a later roadmap step) to assemble a prompt, and by a Worker's or Warden's runtime to
    checkpoint and resume. Calls into hivemind.cell, hivemind.forage, hivemind.llm,
    hivemind.pheromone, hivemind.common and waggle; never hivemind.brood_chamber or
    hivemind.supervision (same layer, no ADR lists that edge -- hivemind.memory.hot_state defines
    its own flat summary models instead of importing Task/Alarm/Question directly).

Key invariants:
    - Every row this package writes carries a HoneyClearance, and `assemble` filters every
      candidate by the reader's allowance before packing (codingrules section 8.9).
    - A state change and its MemoryEvent commit together, in the same transaction, for every write
      in this package (codingrules section 12).
    - Episode records live in the memory tables with a retention window, never on the Pheromone
      Trail (codingrules section 12: "Thoughts are memory, not audit").

See Also:
    - .claude/codingrules.md section 8.9 for this package's whole design.
    - .claude/codingrules.md section 8.8 for why an awake episode assembles rather than
      accumulates its context.
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - README.md "Core concept 7: Memory" for the tier hierarchy this package's v0 slice fits into.
    - hivemind.memory.README for the module-by-module map of this package.

Public API:
    - MemoryTierError, ClearanceError, HandoffNotFoundError, NoteTooLongError: this subsystem's
      error tree (errors).
    - MemoryContext, MemoryIdentity: the collaborators every write function shares (context).
    - TokenCounter, EstimateCounter, ProviderCounter: token counting before a call (counter).
    - Decision, Handoff: the checkpoint document (handoff).
    - Pin, PinSource, add_pin: facts that never decay (pins).
    - Note, add_note, MAX_NOTE_CHARS, MAX_NOTES_PER_AUTHOR: the one thing a bee writes directly
      (notes).
    - EpisodeRecord, EpisodeStream, record_episode, DEFAULT_QUEUE_SIZE: a bee's recorded thinking,
      and its live feed (episodes).
    - write_checkpoint, read_handoff: the write and read paths for a Handoff (checkpoint).
    - Principal, TokenBudget, TriggerEvent, TaskSummary, AlarmSummary, QuestionSummary,
      DecisionSummary, HotStateSources, AssembleRequest, Prompt, assemble, ITEM_CAP_CHARS: hot
      state packing (hot_state).
    - MemoryStore, InMemoryMemoryStore, SqliteMemoryStore, apply_memory_migrations, SUBSYSTEM,
      MIGRATIONS_PACKAGE: the durable half (store).
"""

from hivemind.memory.checkpoint import read_handoff, write_checkpoint
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.counter import EstimateCounter, ProviderCounter, TokenCounter
from hivemind.memory.episodes import (
    DEFAULT_QUEUE_SIZE,
    EpisodeRecord,
    EpisodeStream,
    record_episode,
)
from hivemind.memory.errors import (
    ClearanceError,
    HandoffNotFoundError,
    MemoryTierError,
    NoteTooLongError,
)
from hivemind.memory.handoff import Decision, Handoff
from hivemind.memory.hot_state import (
    ITEM_CAP_CHARS,
    AlarmSummary,
    AssembleRequest,
    DecisionSummary,
    HotStateSources,
    Principal,
    Prompt,
    QuestionSummary,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
    assemble,
)
from hivemind.memory.notes import MAX_NOTE_CHARS, MAX_NOTES_PER_AUTHOR, Note, add_note
from hivemind.memory.pins import Pin, PinSource, add_pin
from hivemind.memory.store import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    InMemoryMemoryStore,
    MemoryStore,
    SqliteMemoryStore,
    apply_memory_migrations,
)

__all__ = [
    "DEFAULT_QUEUE_SIZE",
    "ITEM_CAP_CHARS",
    "MAX_NOTES_PER_AUTHOR",
    "MAX_NOTE_CHARS",
    "MIGRATIONS_PACKAGE",
    "SUBSYSTEM",
    "AlarmSummary",
    "AssembleRequest",
    "ClearanceError",
    "Decision",
    "DecisionSummary",
    "EpisodeRecord",
    "EpisodeStream",
    "EstimateCounter",
    "Handoff",
    "HandoffNotFoundError",
    "HotStateSources",
    "InMemoryMemoryStore",
    "MemoryContext",
    "MemoryIdentity",
    "MemoryStore",
    "MemoryTierError",
    "Note",
    "NoteTooLongError",
    "Pin",
    "PinSource",
    "Principal",
    "Prompt",
    "ProviderCounter",
    "QuestionSummary",
    "SqliteMemoryStore",
    "TaskSummary",
    "TokenBudget",
    "TokenCounter",
    "TriggerEvent",
    "add_note",
    "add_pin",
    "apply_memory_migrations",
    "assemble",
    "read_handoff",
    "record_episode",
    "write_checkpoint",
]
