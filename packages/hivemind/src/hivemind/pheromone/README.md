# hivemind.pheromone

The Pheromone Trail: the Hive's append-only audit log. Every state-changing action anywhere in
the Hive writes a `PheromoneEvent` here before the action counts as complete (codingrules section
12, ADR-0007). Humans audit it, the Observation Hive reads it live, and hot state is partly
derived from it. The trail is one logical log made of per-node segments (`node_id` is one Queen or
Warden process): an offline Warden keeps writing to its own segment and merges it into the Queen's
trail on reconnection.

## Public API

- **Events** (`hivemind.pheromone.events`): `PheromoneEvent` and its eleven family subclasses
  (`CellEvent`, `TaskEvent`, `AlarmEvent`, `ForageEvent`, `MemoryEvent`, `QueenEvent`,
  `WardenEvent`, `ToolEvent`, `SwarmEvent`, `CappingEvent`, `LlmEvent`), the normative `kind`
  vocabulary documented in `events/families.py`, and the JSON codec `parse_event`/
  `parse_event_json`. No event ever carries prompt or completion text; `payload`'s own validator
  enforces that.
- **Trail** (`hivemind.pheromone.trail`): the `PheromoneTrail` protocol (`record`, `query`,
  `export_segment`, `merge_segment`), `TrailQuery` (filters, ordering, limit) and `TrailSegment`
  (one node's exported slice, JSON round-trip preserving each event's real subclass).
  `TRAIL_ORDER_KEY = ("at", "node_id", "id")` is the one place trail order is defined; every
  implementation sorts by it.
- **Memory** (`hivemind.pheromone.memory`): `MemoryPheromoneTrail(clock)`, an in-process
  `PheromoneTrail` for tests and demos, plus `drop_segment(node_id)` (sync), used by
  `MemorySegmentPurge`.
- **SQLite** (`hivemind.pheromone.sqlite`): `SqlitePheromoneTrail`, the durable store, built on
  one table (`pheromone_events`, created by `hivemind.pheromone.migrations`). `insert_event
  (connection, event)` is the primitive another store's own transaction calls so a state change
  and its trail event commit together (codingrules section 12's "same transaction" rule). This
  module issues only `INSERT` and `INSERT OR IGNORE` statements -- never `UPDATE` or `DELETE` --
  which `test_sqlite.py` checks by reading the file's own source.
- **Retention** (`hivemind.pheromone.retention`): the Night Veil boundary and the package's one
  deletion path. `SqliteSegmentPurge`/`MemorySegmentPurge` remove one node's rows;
  `SideChannelPurger` is the protocol the VPN gateway's and Tor daemon's per-Cell connection and
  circuit logs implement in phases 5 and 11; `NightVeilTeardownPurge.purge` runs both, then
  records one `cell.purged` event (counts only) on the Queen's trail.
- **Tail** (`hivemind.pheromone.tail`): `follow(trail, clock, poll_interval_s, since)` yields the
  trail's backlog, then polls forever, never returning on its own; the caller cancels it.

## The Night Veil boundary

A Night Veil Cell's Warden runs inside the Cell, so its node's segment is the Cell's ephemeral
segment. At teardown, `NightVeilTeardownPurge.purge`:

1. Purges that segment (`SegmentPurge.purge_segment`) -- every row this package ever wrote for
   that node id.
2. Purges every registered `SideChannelPurger` -- the VPN gateway's and Tor daemons' own per-Cell
   connection and circuit logs, which are not owned by this package but must go too, or the
   no-retention guarantee has a side channel.
3. Records one `cell.purged` `CellEvent` on the Queen's trail, carrying only the counts
   (`segment_node_id`, `events_purged`, `side_channel_records_purged`) -- never the purged
   content.

This is the only place `hivemind.pheromone` deletes anything. Every other module in the package
only ever inserts.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/pheromone packages/hivemind/tests/contracts
```

Coverage floor is 95% for the pure pieces and 80% for the adapters (codingrules section 14.1):

```bash
uv run --frozen pytest packages/hivemind/tests/unit/pheromone packages/hivemind/tests/contracts \
    --cov=hivemind.pheromone --cov-report=term-missing
```

`tests/contracts/test_pheromone_trail_contract.py` parametrises the same behavioural suite over
`MemoryPheromoneTrail` and `SqlitePheromoneTrail` (the latter on a `tmp_path` SQLite file), plus a
`SegmentPurge` contract over `MemorySegmentPurge` and `SqliteSegmentPurge`. Every test uses
`waggle.clock.FakeClock` and the `new_<kind>_id` minters, never wall-clock time or a random id.
