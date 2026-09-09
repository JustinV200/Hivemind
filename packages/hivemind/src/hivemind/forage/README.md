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

HostCapacity, Seat, RoleFootprint, ForageGrant, ForageRequest and the Forage map are not
implemented yet; they land in phase 3 step 3.12.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/forage
```

Coverage floor is 95% (codingrules section 14.1):

```bash
COVERAGE_FILE=.coverage.forage uv run --frozen pytest -p no:cacheprovider --cov=hivemind.forage \
    --cov-report=term-missing packages/hivemind/tests/unit/forage
```
