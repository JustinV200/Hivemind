# hivemind.memory

Memory v0 (roadmap step 3.14): the working and hot tiers of the Hive's memory hierarchy
(codingrules section 8.9; README "Core concept 7: Memory"). Context is assembled fresh for each
awake episode from durable state; nothing accumulates as a conversation.

## Modules

- `hot_state/` -- flat summary models (`TaskSummary`, `AlarmSummary`, `QuestionSummary`,
  `DecisionSummary`), `Principal`, `TokenBudget`, `TriggerEvent`, the `HotStateSources` protocol a
  caller implements over its own stores, and `assemble`, the pure packing algorithm that turns all
  of that plus pins and notes into a token-budgeted `Prompt`.
- `handoff.py` -- `Decision` and `Handoff`, the resumable snapshot a bee writes before its context
  resets.
- `checkpoint.py` -- `write_checkpoint`/`read_handoff`, the write and read paths for a `Handoff`.
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
  `SqliteMemoryStore` (its two implementations), and the numbered SQL migration series.
- `errors.py` -- `MemoryTierError` (root), `ClearanceError`, `HandoffNotFoundError`,
  `NoteTooLongError`.

## Public API (roadmap step 3.14)

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
