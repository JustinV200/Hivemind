# hivemind.forage

The forage package models capacity as data: HostCapacity, a Seat, RoleFootprint, ForageGrant and
ForageRequest, the Forage map of every source that can serve a model, and the ModelSlot and
Tempo types llm depends on. It never imports llm, so a grant or a routing input can name a model
slot without pulling in the provider machinery.

## Public API (roadmap step 2.3a)

- **Tempo** (`hivemind.forage.tempo`): `AccuracyBar` (`LOW`, `NORMAL`, `HIGH`, `CRITICAL`, mirrors
  `waggle.messages.labels.AccuracyBar`) and `Tempo`, the frozen pydantic model carrying an
  optional latency budget in seconds and an accuracy bar. `Tempo.from_wire`/`to_wire` convert to
  and from `waggle.messages.Tempo`. Read by the Attendant, `llm.routing`, `forage.allocate` and
  Capping (codingrules section 8.14); it never overrides safety.

## Public API (roadmap step 3.4)

- **Slots** (`hivemind.forage.slots`): `ModelSlot` (`QUEEN`, `ATTENDANT`, `WARDEN`, `WORKER`,
  `RIPENER`, `SCAFFOLDER`, `EMBEDDER`, `JUDGE`, `TRANSCRIBER`), whose value is its own
  UPPER_SNAKE name (the wire label) and whose `manifest_key` is the lowercase `[llm.slots]` key;
  `from_wire`/`to_wire` and `from_manifest_key` convert. `Effort` (`LOW`, `MEDIUM`, `HIGH`)
  mirrors `waggle.messages.forage.Effort` and is the per-binding "how hard to think" value a
  grant caps.

## Roadmap step 10.3

`map.slot_for_binding(key, bindings)` resolves an `[llm.slots]` key to the `ModelSlot` it serves:
a slot's own key is that slot, and a named binding (such as `local_worker`) is the first slot, in
`ModelSlot` order, whose fallback chain reaches it; None when no chain does. The Warden's
`slot_binding` point checks `llm:<slot>` for the slot a binding key resolves to.

## Public API (roadmap step 3.12)

Eleven frozen pydantic models, one per Forage concept in codingrules section 6.1's table, split
across `hivemind.forage.models.{capacity,sources,grants,pools}` by responsibility and re-exported
from `hivemind.forage.models` and this package's own `__init__.py`:

- **Capacity** (`models.capacity`): `GpuInfo`, `HostCapacity` (static and live host figures;
  `to_wire`/`from_wire` convert to and from `waggle.messages.reports.HostCapacityReport`, taking a
  `PlatformReport` for the `arch`/`os` fields that report does not carry), `Seat` (concurrent-
  request capacity on one source), `RoleFootprint` (what one bee of a role costs its Cell; no role
  field -- the manifest's `[forage.roles.<role>]` table keys it), `ForageCapacity` (a Cell's
  report: `HostCapacity` plus its `Seat`s plus its own sub-bee cap).
- **Forage map sources** (`models.sources`): `ModelCost`, `ModelSourceSpec` (the static half a
  manifest's `[forage.map.<source_id>]` embeds directly; roadmap step 4.8 adds an optional
  `vram_bytes_required`, read by `hivemind.queen.forage.hosting.write_hosting_plan` against a
  Cell's free VRAM), `Distance` and `Abundance` (the live half). `Abundance` also carries
  `throttled_until` (roadmap step 4.7a): `ForageMap.throttle` masks a source's headroom to zero
  after a hosted provider's `RateLimitedError` until that instant, and the map's read methods
  clear an expired mask with no timer. `ModelSource` (spec plus live figures; `source_ref()`
  builds the wire `SourceRef` other models embed).
- **Grants** (`models.grants`): `AllowedBinding`, `SeatReservation` (both convert to/from their
  wire forms), `ForageGrant` (`to_wire`/`from_wire` convert to/from `GrantIssued`), `ForageRequest`
  (flattens the wire `ForageDelta` into its own fields; roadmap step 4.8 gives `SHARED_SEATS` and
  `SPEND` real ledger-backed handling in `hivemind.queen.forage.requests`), `ForageRequestKind`
  (mirrors the wire enum member for member), `RoyalReserve` (what the Queen holds back before any
  grant; every field defaults so an omitted `[forage.reserve]` section is still safe).
- **Local pool and hosting** (`models.pools`): `LocalPool` (a Warden's own pool; its `reserve`
  field reuses `RoyalReserve`'s shape, scoped to one Cell), `Ceilings` (`to_wire`/`from_wire`
  convert to/from `CeilingsReport`; roadmap step 4.8 adds `scratch_disk_bytes_per_lease` and
  `resident_basket_disk_bytes`, both defaulting to 0), `SourceChain` and `SlotPlan` (name sources
  by id; convert to/from their wire forms given a `source_id -> ModelSource` mapping),
  `HostingPlan` (per Cell, per slot, a chain plus a default; written by
  `hivemind.queen.forage.hosting.write_hosting_plan`, roadmap step 4.8).
- **`ForageMap`** (`hivemind.forage.map`): owns the map's live figures under one `asyncio.Lock`;
  `get`, `sources` and `for_slot` are synchronous reads, `observe` and `set_abundance` are the
  async writers. `SlotBinding` is the forage-side view of one `[llm.slots]` manifest row.
- **`grant`** (`hivemind.forage.allocate`): the pure allocator, `grant(inputs: GrantInputs) ->
  ForageGrant`. `GrantInputs` groups every input (cell capacity, role, footprint, tempo, the map,
  the reserve, `GoalBudgets`, holder, cell id, task id, a minted grant id, `now`, a TTL, and
  roadmap step 4.7's v1 additions -- `goal_sub_bees_used`, `goal_spend_used`,
  `reachable_source_ids`, each defaulted so v0 callers are unaffected) into one frozen dataclass.
  v1 turns the goal's caps into genuinely *remaining* caps, narrows the headroom margin on
  `max_sub_bees` for an urgent tempo (a tight `latency_budget_s`) and on `spend_budget` for a
  thorough one (`HIGH`/`CRITICAL` accuracy), and filters allowed sources by
  `reachable_source_ids` when the caller supplies it. `should_recompute(previous, current,
  threshold)` is v1's other addition: a pure comparison of two `ForageCapacity` readings (free
  memory, free cores) against `[forage] measurement_drift_threshold`, read by the Queen's ledger
  (`hivemind.queen.forage.ledger`) to decide when a live grant needs recomputing. Property-tested
  with hypothesis: a grant never exceeds capacity minus the Royal Reserve, and every allowed
  binding's grade clears `tempo.grade_floor`, under every tempo v1 can draw.
- **`sub_bee_limits`, `goal_limit`, `seat_limit`** (`hivemind.forage.allocate`, the zero-grant
  fix): the five limits `max_sub_bees` is the minimum of, as data. `SubBeeLimits` names each
  (`GrantBound`: `CELL_CAP`, `FREE_CORES`, `FREE_MEMORY`, `SEATS`, `GOAL_BEES`) with its value as
  the figures stand and at their best (an idle host, all of its memory free, every seat free, none
  of the goal's allowance held), plus the headroom margin; `limited_by` is the tightest limit,
  `short()` the limits that leave no whole bee now, `never_lifts()` those that leave none even at
  best. The Queen's dispatcher reads them to tell a passing shortfall (wait) from a lasting one
  (deny at once) without parsing the grant's reason, which now names the tightest limit too.
  `goal_limit` is the goal's own allowance alone and `seat_limit` the model seats alone, for a
  caller with no Cell yet (the dispatcher waits out busy seats before it acquires one).
  Property-tested: the limits' `max_sub_bees` is always the grant's, and no figure at its best
  allows fewer bees.
- **`GrantState`** (`hivemind.forage.grant_state`): `ISSUED -> ACTIVE -> REVOKED`;
  `ACTIVE -> EXHAUSTED -> ACTIVE` (top-up). `can_transition`/`assert_transition` are the only way
  to check or enforce an edge (codingrules Appendix C, "Forage grant" row).
- **`tempo.grade_floor(bar)`**: the minimum Forage map grade a source must clear for an accuracy
  bar (`LOW` -> 1, `NORMAL` -> 2, `HIGH` -> 3, `CRITICAL` -> 4).
- **Errors** (`hivemind.forage.errors`): `ForageError` (root), `UnknownSourceError`,
  `AllocationError`, `InvalidGrantTransitionError`.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/forage
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.forage uv run --frozen pytest -p no:cacheprovider --cov=hivemind.forage \
    --cov-report=term-missing packages/hivemind/tests/unit/forage
```
