# hivemind.memory

The working, hot and warm tiers of the Hive's memory hierarchy (codingrules section 8.9; README
"Core concept 7: Memory"). Context is assembled fresh for each awake episode from durable state;
nothing accumulates as a conversation.

## Modules

- `hot_state/` -- flat summary models (`TaskSummary`, `AlarmSummary`, `QuestionSummary`,
  `DecisionSummary`), `Principal`, `TokenBudget`, `TriggerEvent`, the `HotStateSources` protocol a
  caller implements over its own stores, and `assemble`, the packing algorithm that turns all of
  that plus pins and notes into a token-budgeted `Prompt`, ordered by `relevance.score` (roadmap
  step 4.1).
- `relevance.py` -- `RelevanceScore`, `Scorable` and `score`: pure recency-decay/task-linkage/
  Alarm-severity/pin-floor scoring (roadmap step 4.1), plus `item_id`/`item_timestamp`, the shared
  per-type dispatch `hot_state.packing` and `demote` both read candidates through.
- `demote.py` -- `DemotionReason`, `should_demote` and `demote`: the pure rule for what leaves hot
  state (task closed, Alarm resolved, aged past the manifest's `hot_window_s`) and the one write
  path that archives an item into Bee Bread (roadmap step 4.2).
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
  `SqliteMemoryStore` (its two implementations), and the numbered SQL migration series (five
  tables: pins, notes, handoffs, episodes, bee_bread).
- `errors.py` -- `MemoryTierError` (root), `ClearanceError`, `HandoffNotFoundError`,
  `NoteTooLongError`, `BeeBreadEntryNotFoundError`, `SummaryOfSummaryError`,
  `EmptyCompactionError`, `TooManySourcesError`.

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
this package.
