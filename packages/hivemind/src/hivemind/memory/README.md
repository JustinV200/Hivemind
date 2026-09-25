# hivemind.memory

The working, hot and warm tiers of the Hive's memory hierarchy (codingrules section 8.9; README
"Core concept 7: Memory"). Context is assembled fresh for each awake episode from durable state;
nothing accumulates as a conversation.

## Modules

- `hot_state/` -- flat summary models (`TaskSummary`, `AlarmSummary`, `QuestionSummary`,
  `DecisionSummary`, `CellWaxSummary`), `Principal`, `TokenBudget`, `TriggerEvent`, the
  `HotStateSources` protocol a caller implements over its own stores (its `wax(cells)` method
  returns WRITTEN Cell Wax only for a Cell in `cells`; its `handoff()` method returns the Handoff
  the episode is resuming from, or None, rendered by `assemble` into its own delimited, bounded
  block of `HOT_STATE` regardless of budget), and `assemble`, the packing algorithm that turns all
  of that plus pins and notes into a token-budgeted `Prompt`, ordered by `relevance.score` (roadmap
  step 4.1), gated by `AssembleRequest.cells_in_play` for wax (roadmap step 4.2a). The cold tier
  (roadmap step 7.7) packs last: `AssembleRequest.retrieved` carries the Honey hits a caller
  retrieved (a Drone passes its `TaskAssign.honey`), and `hot_state/retrieved.py` drops any hit
  above the principal's clearance, renders the rest best score first (`render_hit`: a
  `[honey <ref>] <title>` header, scope, clearance, provenance and score, then the excerpt capped
  at `item_cap_chars`, with delimiter-shaped runs spaced out) under `RETRIEVED_PREAMBLE`, and packs
  them into whatever hot state left, never past `TokenBudget.retrieved_fraction` of the packing
  target. They become the `RETRIEVED` section and are listed in `Prompt.included`/`dropped` as
  `honey:<honey_ref>`; `on_drop` never sees a hit (it already lives in the Honey Store). Memory
  never imports `hivemind.honey_store`: hits arrive as `waggle.messages.honey.HoneyHit` values.
- `relevance.py` -- `RelevanceScore`, `Scorable` and `score`: pure recency-decay/task-linkage/
  Alarm-or-Cell-Wax-severity/pin-floor scoring (roadmap steps 4.1, 4.2a), plus `item_id`/
  `item_timestamp`, the shared per-type dispatch `hot_state.packing` and `demote` both read
  candidates through.
- `demote.py` -- `DemotionReason`, `should_demote` and `demote`: the pure rule for what leaves hot
  state (task closed, Alarm resolved, aged past the manifest's `hot_window_s`) and the one write
  path that archives an item into Bee Bread (roadmap step 4.2); never returns a reason for a Pin
  or a `CellWaxSummary` (Cell Wax has its own lifecycle, in `cell_wax/`).
- `cell_wax/` -- `CellWax` (a Queen-written caution about one Cell, roadmap step 4.2a),
  `WaxSeverity` (mirrored from `waggle.messages.cell.wax` member for member), `WaxState` and its
  `TRANSITIONS` (`PROPOSED -> WRITTEN -> CLEARED | EXPIRED`, `PROPOSED -> REJECTED`, Appendix C),
  and the five functions that walk it: `propose_wax`, `write_wax`, `reject_wax`, `clear_wax`,
  `expire_wax`, plus `retire_wax_for_cell` (phase 5's own forward-looking hook) and
  `cap_wax_for_hot_state` (the per-Cell hot-state cap, highest severity then newest,
  docs/adr/0022).
- `compact.py` -- `CompactionSchema`, `CompactionRequest`, `CompactionDeps`, `CompactionResult` and
  `compact`: folds a batch of Bee Bread entries into one new `SUMMARY` entry on `ModelSlot.RIPENER`,
  never from a previous summary, with every pin copied verbatim (roadmap step 4.3, docs/adr/0022).
  The other half of a House Bee sweep (`hivemind.workers.roles.house_bee`), demotion being the
  first.
- `bee_bread/` -- `BeeBreadEntry`/`BeeBreadEntryKind` (the warm tier's one row shape), `BeeBread`
  (the lookup-only index: by id, by task, between two times), and `deposit_transcript`/
  `deposit_tool_result`/`deposit_handoff_ref`/`deposit_hot_state_item` (every write path, roadmap
  step 4.2).
- `handoff.py` -- `Decision` and `Handoff`, the resumable snapshot a bee writes before its context
  resets.
- `checkpoint.py` -- `write_checkpoint`/`read_handoff`, the write and read paths for a `Handoff`;
  `write_checkpoint` also deposits the Handoff (and, when given one, its transcript) into Bee Bread.
- `pins.py` -- `Pin`, `PinSource` and `add_pin`: facts that never decay out of hot state.
- `notes.py` -- `Note` and `add_note`: the one memory-tier row a bee writes on its own initiative,
  bounded per author.
- `episodes.py` -- `EpisodeRecord`, `record_episode` and `EpisodeStream`: a record of every awake
  episode's or autopilot decision's thinking, and a live feed of it for the Observation Hive.
- `counter.py` -- `TokenCounter`, `EstimateCounter` and `ProviderCounter`: how a candidate prompt
  section gets token-counted before packing decides whether it fits.
- `context.py` -- `MemoryContext`/`MemoryIdentity`: the store, identity and clock every writer
  function in this package shares.
- `store/` -- `MemoryStore` (the persistence protocol), `InMemoryMemoryStore` and
  `SqliteMemoryStore` (its two implementations), and the numbered SQL migration series (six
  tables: pins, notes, handoffs, episodes, bee_bread, cell_wax).
- `errors.py` -- `MemoryTierError` (root), `ClearanceError`, `HandoffNotFoundError`,
  `NoteTooLongError`, `BeeBreadEntryNotFoundError`, `SummaryOfSummaryError`,
  `EmptyCompactionError`, `TooManySourcesError`, `InvalidWaxTransitionError`,
  `WaxTextTooLongError`, `WaxNotFoundError`.

## Public API

See the `Public API:` section of `__init__.py` for the full, current list; the summary above names
each name's home module.

## How to test this

```
uv run --frozen pytest packages/hivemind/tests/unit/memory
uv run --frozen pytest packages/hivemind/tests/contracts/test_memory_store_contract.py
```

`tests/unit/memory/` mirrors this package module for module. `tests/contracts/
test_memory_store_contract.py` runs the `MemoryStore` contract over both `InMemoryMemoryStore` and
`SqliteMemoryStore` (a temp SQLite file per test, with the Pheromone Trail's own migrations applied
first, matching `hivemind.cli.stores`). `tests/builders/memory.py` has a builder for every model in
this package. `tests/e2e/test_flood.py` floods `assemble` with ten thousand Alarms, and with ten
thousand retrieved hits on top of them, and proves neither flood ever breaks the budget nor the
retrieved section's own share:

```
uv run --frozen pytest packages/hivemind/tests/e2e/test_flood.py
```
