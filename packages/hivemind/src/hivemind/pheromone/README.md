# hivemind.pheromone

The Pheromone Trail: the Hive's append-only audit log. Every state-changing action anywhere in
the Hive writes a `PheromoneEvent` here before the action counts as complete (codingrules section
12, ADR-0007). Humans audit it, the Observation Hive reads it live, and hot state is partly
derived from it. The trail is one logical log made of per-node segments (`node_id` is one Queen or
Warden process): an offline Warden keeps writing to its own segment and merges it into the Queen's
trail on reconnection.

## Public API

- **Events** (`hivemind.pheromone.events`): `PheromoneEvent` and its thirteen family subclasses
  (`CellEvent`, `TaskEvent`, `AlarmEvent`, `ForageEvent`, `MemoryEvent`, `QueenEvent`,
  `WardenEvent`, `ToolEvent`, `SwarmEvent`, `CappingEvent`, `LlmEvent`, `WorkerEvent`,
  `GuardEvent`), the normative `kind`
  vocabulary documented in the `events/families/` package (a package since roadmap step 10.5 added
  the Queen's goal request and chat kinds: `work.py`, `resources.py` and `supervisors.py` each
  document their own families' kinds, `codec.py` holds the registry), and the JSON codec
  `parse_event`/`parse_event_json`. No event ever carries prompt or completion text; `payload`'s
  own validator enforces that.
- **Trail** (`hivemind.pheromone.trail`): a package because the protocol, its two implementations
  and live-tail follow together pushed past a single file (codingrules 5.6). `trail/__init__.py`
  is its face: a caller writes `from hivemind.pheromone.trail import PheromoneTrail` (or straight
  from `hivemind.pheromone`) without knowing the split.
  - `trail/protocol.py` -- the `PheromoneTrail` protocol (`record`, `query`, `export_segment`,
    `merge_segment`), `TrailQuery` (filters, ordering, limit) and `TrailSegment` (one node's
    exported slice, JSON round-trip preserving each event's real subclass).
    `TRAIL_ORDER_KEY = ("at", "node_id")` is the one place trail order is defined; every
    implementation sorts by it and keeps events that tie on both in the order they were recorded
    (a stable sort in memory, `rowid` in SQLite), because ids minted within one millisecond carry
    random tails and would shuffle an audit log.
  - `trail/memory.py` -- `MemoryPheromoneTrail(clock)`, an in-process `PheromoneTrail` for tests
    and demos, plus `drop_segment(node_id)` (sync), used by `MemorySegmentPurge`.
  - `trail/sqlite.py` -- `SqlitePheromoneTrail`, the durable store, built on one table
    (`pheromone_events`, created by `trail/migrations/`). `insert_event(connection, event)` is the
    primitive another store's own transaction calls so a state change and its trail event commit
    together (codingrules section 12's "same transaction" rule). This module issues only `INSERT`
    and `INSERT OR IGNORE` statements -- never `UPDATE` or `DELETE` -- which `test_sqlite.py`
    checks by reading the file's own source.
  - `trail/migrations/` -- the numbered SQL migration series `trail/sqlite.py` applies
    (`0001_create_pheromone_events.sql`); a real Python package (an `__init__.py`, however empty)
    because `importlib.resources` addresses it by dotted name.
  - `trail/tail.py` -- `follow(trail, clock, poll_interval_s, since)` yields the trail's backlog,
    then polls forever, never returning on its own; the caller cancels it.
- **Retention** (`hivemind.pheromone.retention`): the Night Veil boundary and the package's one
  deletion path; a package since the boundary grew its production half, split by responsibility:
  - `retention/skeleton.py` -- what survives a Night Veil Cell: `SKELETON_KINDS`, and
    `skeleton_event`, which cuts an event's payload to the fields the skeleton keeps (or returns
    None for a kind that never survives); `tier_counts` folds a Cell's `capping.*` detail into
    one `TierCount` per risk tier. Pure.
  - `retention/segments.py` -- `EphemeralSegments`: each living Night Veil Cell's segment, held
    in the Queen's memory, and the index of which ids (its Warden, nodes, grants, tasks) belong to
    which Cell. A segment is readable per Cell while the Cell lives (`query`) and is never merged
    into the durable trail.
  - `retention/trail.py` -- `VeiledTrail`, the `PheromoneTrail` decorator every Queen-side writer
    records through: an event about no Night Veil Cell reaches the durable trail unchanged; an
    event about one sends only its skeleton copy there and the whole event to the Cell's segment.
    `segments_of(trail)` returns the segments behind it.
  - `retention/purge.py` -- `NightVeilTeardownPurge` and its collaborators:
    `SqliteSegmentPurge`/`LazySqliteSegmentPurge`/`MemorySegmentPurge` remove one node's rows;
    `SideChannels` names every store beyond the trail a teardown must clear (`vpn_gateway`,
    `tor_hidden_service`, `nectar`, `honey`), each a `SideChannelPurger` once its store exists.

## The Night Veil boundary

Codingrules section 12: a Night Veil Cell's execution records live in an ephemeral segment keyed
to the Cell and purged at teardown; only the lifecycle skeleton survives on the Queen's trail.

While the Cell lives, nothing about it reaches the durable trail beyond the skeleton:

- Its Warden runs inside the Cell and keeps its own trail in memory (`MemoryPheromoneTrail`, built
  by `hivemind.cli.in_cell.main`), which dies with the container. The segments it ships are handed
  to `EphemeralSegments.merge` by the Queen's segment receiver (`hivemind.queen.trail.sync`), never
  to the durable trail.
- Every Queen-side writer records through the `VeiledTrail` the composition root builds
  (`hivemind.cli.compose.night_veil`) whenever a Virtual side exists. The Cell's segment opens the
  moment the lifecycle provisions it (`hivemind.hive.night_veil.boundary`); its Warden is filed at
  attach, a Night Veil goal's tasks are expected at planning, and a task is bound to its Cell when
  it is placed, so each of their records is routed by id.
- The Brood Chamber writes a task's event in the same transaction as its row, past any trail
  decorator, so it cuts a Night Veil task's event at the source: the transition and the task id,
  nothing else.

The skeleton, with the payload each kind keeps (`retention/skeleton.py`): `cell.provisioned`
(backend, image, tier), `cell.attested` (the verdict, the red checks, and each check's PASS or
FAIL without its detail), `queen.placed` (the human request's id), `forage.granted` (task id,
sub-bee count), `forage.plan_written` (revision, slot count), `cell.sting_cut`, every task kind
(nothing beyond the task id), `capping.summary` (tier and the approved, rejected and rolled-back
counts) and `cell.destroyed` (the Undertaker's cleanup counts).

Every path a Night Veil Cell ends runs `NightVeilTeardownPurge.purge`: a lifecycle teardown (the
task finished, a provision failed after the Cell existed, the Hive shut down), an Absconding
(`hive cells abscond`), and a Queen restart whose reconcile finds the Cell gone. The purge:

1. Takes the Cell's ephemeral segment whole, first, so no later failure leaves it in memory.
2. Records one `capping.summary` per risk tier, folded from the segment's `capping.*` detail.
3. Removes any row a node of the Cell's own left on the durable trail (`SegmentPurge.purge_segment`,
   the package's one `DELETE`): a segment merged by a Queen older than this boundary.
4. Runs every registered side channel under `SIDE_CHANNEL_TIMEOUT_S`. None is registered today:
   the VPN client, the Tor daemon and their logs run inside the Cell and die with it; the Hive
   Stand's own hidden-service log (phase 11), Nectar and Honey (phase 7) are named seams on
   `SideChannels`. What a Cell's backend keeps after destroy is the backend's to remove
   (`hivemind.hive`'s README, "Night Veil: what a Cell itself keeps").
5. Records one `cell.purged` event on the Queen's trail carrying only `events_purged` and
   `side_channel_records_purged`, never anything purged.

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

`tests/unit/pheromone/` mirrors this layout: `events/`, `trail/` (`test_protocol.py`,
`test_memory.py`, `test_sqlite.py`, `test_tail.py`), `retention/` (`test_skeleton.py`, one test
per payload cut, `test_segments.py`, `test_trail.py`, `test_purge.py`), and `test_errors.py` for
the one module that stayed flat. `tests/e2e/test_night_veil_boundary.py` runs the whole boundary
through a Hive, on every end path.

`tests/contracts/test_pheromone_trail_contract.py` parametrises the same behavioural suite over
`MemoryPheromoneTrail` and `SqlitePheromoneTrail` (the latter on a `tmp_path` SQLite file), plus a
`SegmentPurge` contract over `MemorySegmentPurge`, `SqliteSegmentPurge` and
`LazySqliteSegmentPurge`. Every test uses
`waggle.clock.FakeClock` and the `new_<kind>_id` minters, never wall-clock time or a random id.
