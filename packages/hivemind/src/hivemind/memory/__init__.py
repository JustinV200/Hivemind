"""Memory: assemble hot state into a prompt, score relevance, demote and archive into Bee Bread.

Context is treated like a cache hierarchy (README "Core concept 7: Memory"): what any bee's model
sees is assembled fresh for each awake episode from durable state, never accumulated as a
conversation (codingrules section 8.8: "Awake episodes are stateless"). This package covers the
working, hot and warm tiers of that hierarchy. `hot_state` packs active tasks, open Alarms, pending
questions, recent decisions, pins and notes into a token-budgeted `Prompt`, ordered by
`relevance.score` (roadmap step 4.1: recency decay, task linkage, Alarm severity, a pin's own
non-decaying floor). `handoff` is the resumable snapshot a bee writes before its context resets
(`Handoff`); `checkpoint` writes and reads one (`write_checkpoint`/`read_handoff`), and deposits it
(and, when given one, its transcript) into `bee_bread` (roadmap step 4.2: the warm tier, an index
over Brood Chamber/the trail by id, time and task, plus stored Handoffs and deposited transcripts,
lookup only, no search). `demote` is the pure rule for what leaves hot state (a closed task, a
resolved Alarm, or age past the manifest's `hot_window_s`) and the one write path that moves an
item into Bee Bread. `compact` (roadmap step 4.3) folds a batch of Bee Bread entries into one new
summary entry on `ModelSlot.RIPENER`, never from a previous summary and with every pin copied
verbatim (docs/adr/0022); it is the other half of a House Bee sweep (`hivemind.workers.roles.
house_bee`, a sibling dispatch), demotion being the first. `pins` and `notes` are the two ways a
fact enters hot state without going through a whole episode (`Pin`, `Note`); `episodes` records
every awake episode's or autopilot
decision's thinking, streamable live to the Observation Hive (`EpisodeRecord`, `EpisodeStream`);
`counter` estimates or counts tokens before a call is made (`TokenCounter`); `context` bundles the
collaborators every write shares (`MemoryContext`); `cell_wax` (roadmap step 4.2a) holds `CellWax`,
a Queen-written caution about one Cell, its state machine (`PROPOSED -> WRITTEN ->
CLEARED | EXPIRED`, `PROPOSED -> REJECTED`) and the five functions that walk it; it enters hot
state, through `hot_state`'s own `CellWaxSummary` and `HotStateSources.wax`, only while its Cell is
in `AssembleRequest.cells_in_play`. `store` is the durable half, six SQLite tables plus an
in-memory fake for tests. `taint` (roadmap step 10.6d, ADR-0035) is the one label that keeps memory
written while a bee may have been compromised out of every later prompt: `taint_memory` sets it on
every checkpoint, Handoff, episode record and Bee Bread deposit a `TaintScope` covers, and only
`clear_taint`, on a judge verdict, clears it; until then `assemble`, every Bee Bread lookup,
`list_episodes` and `read_handoff` refuse the item. `hot_state` also renders outside text under the
untrusted-content scanner's verdict (`render_untrusted`, roadmap 10.6b). Bee terms used here: a
**Handoff** is a structured document a bee writes to resume its own work later (possibly on a
different model or host); **hot state** is the
always-loaded, bounded slice of memory an episode's prompt is built from; **Bee Bread** is the warm
tier bees ferment pollen into so it keeps until needed -- what leaves hot state, findable by id,
time or task; a **Pin** is a fact that never decays out of hot state; **Cell Wax** marks a Cell (it
is not the honey inside); **Honey clearance** (`hivemind.cell.HoneyClearance`) is the
data-sensitivity label every row in this package carries.

Fits into the Hive:
    Layer 2 (the Cell abstraction, state, memory, policy). Called by queen.awake and wardens.awake
    to assemble a prompt, and by a Worker's or Warden's runtime to checkpoint and resume. Calls
    into hivemind.cell, hivemind.forage, hivemind.llm, hivemind.pheromone, hivemind.common and
    waggle; never hivemind.brood_chamber or hivemind.supervision (same layer, no ADR lists that
    edge -- hivemind.memory.hot_state defines its own flat summary models instead of importing
    Task/Alarm/Question directly).

Key invariants:
    - Every row this package writes carries a HoneyClearance, and `assemble` filters every
      candidate by the reader's allowance before packing (codingrules section 8.9).
    - A state change and its MemoryEvent commit together, in the same transaction, for every write
      in this package (codingrules section 12).
    - Episode records live in the memory tables with a retention window, never on the Pheromone
      Trail (codingrules section 12: "Thoughts are memory, not audit").
    - A TAINTED item never reaches a prompt or a resumed bee, and only `hivemind.memory.taint`
      writes the label (roadmap 10.6d).

See Also:
    - .claude/codingrules.md section 8.9 for this package's whole design.
    - .claude/codingrules.md section 8.8 for why an awake episode assembles rather than
      accumulates its context.
    - .claude/codingrules.md section 4 for the layer 2 row this package occupies.
    - README.md "Core concept 7: Memory" for the tier hierarchy this package's slice fits into.
    - hivemind.memory.README for the module-by-module map of this package.

Public API:
    - MemoryTierError, ClearanceError, HandoffNotFoundError, NoteTooLongError,
      BeeBreadEntryNotFoundError, SummaryOfSummaryError, EmptyCompactionError,
      TooManySourcesError, InvalidWaxTransitionError, WaxTextTooLongError, WaxNotFoundError,
      TaintedMemoryError, InvalidTaintTransitionError, TaintTargetNotFoundError, TaintJudgeError:
      this subsystem's error tree (errors).
    - MemoryContext, MemoryIdentity: the collaborators every write function shares (context).
    - CellWax, WaxSeverity, WaxState, WaxProposalInput, MAX_WAX_REASON_CHARS, MAX_WAX_TEXT_CHARS,
      propose_wax, write_wax, reject_wax, clear_wax, expire_wax, retire_wax_for_cell,
      cap_wax_for_hot_state: Cell Wax, the note and its state machine (cell_wax).
    - TokenCounter, EstimateCounter, ProviderCounter: token counting before a call (counter).
    - Decision, Handoff: the checkpoint document (handoff).
    - Pin, PinSource, add_pin: facts that never decay (pins).
    - Note, add_note, MAX_NOTE_CHARS, MAX_NOTES_PER_AUTHOR: the one thing a bee writes directly
      (notes).
    - EpisodeRecord, EpisodeStream, record_episode, DEFAULT_QUEUE_SIZE: a bee's recorded thinking,
      and its live feed (episodes).
    - write_checkpoint, read_handoff: the write and read paths for a Handoff (checkpoint).
    - Principal, TokenBudget, TriggerEvent, TaskSummary, AlarmSummary, QuestionSummary,
      DecisionSummary, CellWaxSummary, HotStateSources, AssembleRequest, Prompt, assemble,
      ITEM_CAP_CHARS, RESUMED_HANDOFF_ID: hot state packing (hot_state).
    - UntrustedText, RetrievedItem, RetrievedKind, render_untrusted, render_retrieved, within_scan:
      outside text under its scan verdict, and the phase 7 retrieval seam (hot_state, roadmap
      10.6b).
    - TaintMarker, TaintSource, TaintState, TaintedKind, TaintTarget, TaintScope, TaintStamp,
      TaintReport, TaintLedger, TaintableItem, taint_memory, is_refused, TaintJudge,
      ModelTaintJudge, TaintReview, TaintVerdict, TaintJudgement, TAINT_RUBRIC_ID, clear_taint,
      TaintClearRequest, TaintClearDeps, TaintClearResult, ClearOutcome, TaintedNectarRipener: the
      taint label, its one setter and its one clearer (taint, roadmap 10.6d).
    - RelevanceScore, Scorable, score, item_id, item_timestamp, RECENCY_HALF_LIFE_S,
      TASK_LINKAGE_BONUS, PIN_FLOOR: relevance scoring (relevance).
    - DemotionReason, should_demote, demote: what leaves hot state, and the write path (demote).
    - CompactionSchema, CompactionRequest, CompactionDeps, CompactionResult, compact,
      MAX_SUMMARY_CHARS, MAX_KEY_FACTS, MAX_KEY_FACT_CHARS, MAX_OPEN_THREADS,
      MAX_OPEN_THREAD_CHARS, RIPENER_OUTPUT_TOKENS: summarise Bee Bread entries into one summary
      (compact).
    - BeeBreadEntry, BeeBreadEntryKind, BeeBread, deposit_transcript, deposit_tool_result,
      deposit_handoff_ref, deposit_hot_state_item, deposit_dropped_items: the warm tier
      (bee_bread).
    - MemoryStore, InMemoryMemoryStore, SqliteMemoryStore, apply_memory_migrations, SUBSYSTEM,
      MIGRATIONS_PACKAGE: the durable half (store).
    - MAX_OVERFLOWS, SHRINK_FACTOR, MIN_BUDGET_TOKENS, ContextOverflowError, shrink,
      run_with_overflow_retry: overflow recovery (overflow, roadmap step 4.4).
    - MAX_COMPACT_VIEW_CHARS, InterventionKind, Thresholds, intervention_kind_for,
      capped_compact_view: the compact/handoff threshold rule and capped inspection view
      (thresholds, roadmap step 4.6).
"""

from hivemind.memory.bee_bread import (
    BeeBread,
    BeeBreadEntry,
    BeeBreadEntryKind,
    deposit_dropped_items,
    deposit_handoff_ref,
    deposit_hot_state_item,
    deposit_tool_result,
    deposit_transcript,
)
from hivemind.memory.cell_wax import (
    MAX_WAX_REASON_CHARS,
    MAX_WAX_TEXT_CHARS,
    CellWax,
    WaxProposalInput,
    WaxSeverity,
    WaxState,
    cap_wax_for_hot_state,
    clear_wax,
    expire_wax,
    propose_wax,
    reject_wax,
    retire_wax_for_cell,
    write_wax,
)
from hivemind.memory.checkpoint import read_handoff, write_checkpoint
from hivemind.memory.compact import (
    MAX_KEY_FACT_CHARS,
    MAX_KEY_FACTS,
    MAX_OPEN_THREAD_CHARS,
    MAX_OPEN_THREADS,
    MAX_SUMMARY_CHARS,
    RIPENER_OUTPUT_TOKENS,
    CompactionDeps,
    CompactionRequest,
    CompactionResult,
    CompactionSchema,
    compact,
)
from hivemind.memory.context import MemoryContext, MemoryIdentity
from hivemind.memory.counter import EstimateCounter, ProviderCounter, TokenCounter
from hivemind.memory.demote import DemotionReason, demote, should_demote
from hivemind.memory.episodes import (
    DEFAULT_QUEUE_SIZE,
    EpisodeRecord,
    EpisodeStream,
    record_episode,
)
from hivemind.memory.errors import (
    BeeBreadEntryNotFoundError,
    ClearanceError,
    EmptyCompactionError,
    HandoffNotFoundError,
    InvalidTaintTransitionError,
    InvalidWaxTransitionError,
    MemoryTierError,
    NoteTooLongError,
    SummaryOfSummaryError,
    TaintedMemoryError,
    TaintJudgeError,
    TaintTargetNotFoundError,
    TooManySourcesError,
    WaxNotFoundError,
    WaxTextTooLongError,
)
from hivemind.memory.handoff import Decision, Handoff
from hivemind.memory.hot_state import (
    ITEM_CAP_CHARS,
    RESUMED_HANDOFF_ID,
    AlarmSummary,
    AssembleRequest,
    CellWaxSummary,
    DecisionSummary,
    HotStateSources,
    Principal,
    Prompt,
    QuestionSummary,
    RetrievedItem,
    RetrievedKind,
    TaskSummary,
    TokenBudget,
    TriggerEvent,
    UntrustedText,
    assemble,
    render_retrieved,
    render_untrusted,
    within_scan,
)
from hivemind.memory.notes import MAX_NOTE_CHARS, MAX_NOTES_PER_AUTHOR, Note, add_note
from hivemind.memory.overflow import (
    MAX_OVERFLOWS,
    MIN_BUDGET_TOKENS,
    SHRINK_FACTOR,
    ContextOverflowError,
    run_with_overflow_retry,
    shrink,
)
from hivemind.memory.pins import Pin, PinSource, add_pin
from hivemind.memory.relevance import (
    PIN_FLOOR,
    RECENCY_HALF_LIFE_S,
    TASK_LINKAGE_BONUS,
    RelevanceScore,
    Scorable,
    item_id,
    item_timestamp,
    score,
)
from hivemind.memory.store import (
    MIGRATIONS_PACKAGE,
    SUBSYSTEM,
    InMemoryMemoryStore,
    MemoryStore,
    SqliteMemoryStore,
    apply_memory_migrations,
)
from hivemind.memory.taint import (
    TAINT_RUBRIC_ID,
    ClearOutcome,
    ModelTaintJudge,
    TaintableItem,
    TaintClearDeps,
    TaintClearRequest,
    TaintClearResult,
    TaintedKind,
    TaintedNectarRipener,
    TaintJudge,
    TaintJudgement,
    TaintLedger,
    TaintMarker,
    TaintReport,
    TaintReview,
    TaintScope,
    TaintSource,
    TaintStamp,
    TaintState,
    TaintTarget,
    TaintVerdict,
    clear_taint,
    is_refused,
    taint_memory,
)
from hivemind.memory.thresholds import (
    MAX_COMPACT_VIEW_CHARS,
    InterventionKind,
    Thresholds,
    capped_compact_view,
    intervention_kind_for,
)

__all__ = [
    "DEFAULT_QUEUE_SIZE",
    "ITEM_CAP_CHARS",
    "MAX_COMPACT_VIEW_CHARS",
    "MAX_KEY_FACTS",
    "MAX_KEY_FACT_CHARS",
    "MAX_NOTES_PER_AUTHOR",
    "MAX_NOTE_CHARS",
    "MAX_OPEN_THREADS",
    "MAX_OPEN_THREAD_CHARS",
    "MAX_OVERFLOWS",
    "MAX_SUMMARY_CHARS",
    "MAX_WAX_REASON_CHARS",
    "MAX_WAX_TEXT_CHARS",
    "MIGRATIONS_PACKAGE",
    "MIN_BUDGET_TOKENS",
    "PIN_FLOOR",
    "RECENCY_HALF_LIFE_S",
    "RESUMED_HANDOFF_ID",
    "RIPENER_OUTPUT_TOKENS",
    "SHRINK_FACTOR",
    "SUBSYSTEM",
    "TAINT_RUBRIC_ID",
    "TASK_LINKAGE_BONUS",
    "AlarmSummary",
    "AssembleRequest",
    "BeeBread",
    "BeeBreadEntry",
    "BeeBreadEntryKind",
    "BeeBreadEntryNotFoundError",
    "CellWax",
    "CellWaxSummary",
    "ClearOutcome",
    "ClearanceError",
    "CompactionDeps",
    "CompactionRequest",
    "CompactionResult",
    "CompactionSchema",
    "ContextOverflowError",
    "Decision",
    "DecisionSummary",
    "DemotionReason",
    "EmptyCompactionError",
    "EpisodeRecord",
    "EpisodeStream",
    "EstimateCounter",
    "Handoff",
    "HandoffNotFoundError",
    "HotStateSources",
    "InMemoryMemoryStore",
    "InterventionKind",
    "InvalidTaintTransitionError",
    "InvalidWaxTransitionError",
    "MemoryContext",
    "MemoryIdentity",
    "MemoryStore",
    "MemoryTierError",
    "ModelTaintJudge",
    "Note",
    "NoteTooLongError",
    "Pin",
    "PinSource",
    "Principal",
    "Prompt",
    "ProviderCounter",
    "QuestionSummary",
    "RelevanceScore",
    "RetrievedItem",
    "RetrievedKind",
    "Scorable",
    "SqliteMemoryStore",
    "SummaryOfSummaryError",
    "TaintClearDeps",
    "TaintClearRequest",
    "TaintClearResult",
    "TaintJudge",
    "TaintJudgeError",
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
    "TaintTargetNotFoundError",
    "TaintVerdict",
    "TaintableItem",
    "TaintedKind",
    "TaintedMemoryError",
    "TaintedNectarRipener",
    "TaskSummary",
    "Thresholds",
    "TokenBudget",
    "TokenCounter",
    "TooManySourcesError",
    "TriggerEvent",
    "UntrustedText",
    "WaxNotFoundError",
    "WaxProposalInput",
    "WaxSeverity",
    "WaxState",
    "WaxTextTooLongError",
    "add_note",
    "add_pin",
    "apply_memory_migrations",
    "assemble",
    "cap_wax_for_hot_state",
    "capped_compact_view",
    "clear_taint",
    "clear_wax",
    "compact",
    "demote",
    "deposit_dropped_items",
    "deposit_handoff_ref",
    "deposit_hot_state_item",
    "deposit_tool_result",
    "deposit_transcript",
    "expire_wax",
    "intervention_kind_for",
    "is_refused",
    "item_id",
    "item_timestamp",
    "propose_wax",
    "read_handoff",
    "record_episode",
    "reject_wax",
    "render_retrieved",
    "render_untrusted",
    "retire_wax_for_cell",
    "run_with_overflow_retry",
    "score",
    "should_demote",
    "shrink",
    "taint_memory",
    "within_scan",
    "write_checkpoint",
    "write_wax",
]
