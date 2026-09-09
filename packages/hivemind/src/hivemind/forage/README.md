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
  manifest's `[forage.map.<source_id>]` embeds directly), `Distance` and `Abundance` (the live
  half), `ModelSource` (spec plus live figures; `source_ref()` builds the wire `SourceRef` other
  models embed).
- **Grants** (`models.grants`): `AllowedBinding`, `SeatReservation` (both convert to/from their
  wire forms), `ForageGrant` (`to_wire`/`from_wire` convert to/from `GrantIssued`), `ForageRequest`
  (flattens the wire `ForageDelta` into its own fields), `ForageRequestKind` (mirrors the wire enum
  member for member), `RoyalReserve` (what the Queen holds back before any grant; every field
  defaults so an omitted `[forage.reserve]` section is still safe).
- **Local pool and hosting** (`models.pools`): `LocalPool` (a Warden's own pool; its `reserve`
  field reuses `RoyalReserve`'s shape, scoped to one Cell), `Ceilings` (`to_wire`/`from_wire`
  convert to/from `CeilingsReport`), `SourceChain` and `SlotPlan` (name sources by id; convert
  to/from their wire forms given a `source_id -> ModelSource` mapping), `HostingPlan` (per Cell,
  per slot, a chain plus a default).
- **`ForageMap`** (`hivemind.forage.map`): owns the map's live figures under one `asyncio.Lock`;
  `get`, `sources` and `for_slot` are synchronous reads, `observe` and `set_abundance` are the
  async writers. `SlotBinding` is the forage-side view of one `[llm.slots]` manifest row.
- **`grant`** (`hivemind.forage.allocate`): the pure v0 allocator, `grant(inputs: GrantInputs) ->
  ForageGrant`. `GrantInputs` groups every input (cell capacity, role, footprint, tempo, the map,
  the reserve, `GoalBudgets`, holder, cell id, task id, a minted grant id, `now` and a TTL) into
  one frozen dataclass. Property-tested with hypothesis: a grant never exceeds capacity minus the
  Royal Reserve, and every allowed binding's grade clears `tempo.grade_floor`.
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
