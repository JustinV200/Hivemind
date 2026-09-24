# hivemind.manifest

The manifest package loads and validates the Hive Manifest: the TOML file that configures one
running Hive. It never imports `hivemind.llm` or `hivemind.cell` (codingrules section 4 ranks both
above or beside `manifest` in the layer table), so `[llm.providers.*].capabilities` overrides and
the `[security]`/`[honey.clearance]` tier enums are handled as plain data or waggle's own wire
enums rather than the hivemind-side mirrors those two packages own.

## Public API (roadmap step 3.1)

- **Schema** (`hivemind.manifest.schema`, a package because fourteen documented sections exceed
  the 300-line file limit): `HiveManifest`, the root model, plus every section --
  `HiveSection`, `QueenSection`, `HiveStandSection` + `HiveStandCapacityOverrides`,
  `BroodChamberSection`, `PheromoneSection` (`schema.core`); `LlmSection`, `ProviderKind`,
  `ProviderSpec`, `CapabilityOverrides`, `SlotBinding` (`schema.llm`); `ForageSection`
  (`schema.forage`, embedding `hivemind.forage`'s `RoleFootprint`, `ModelSourceSpec` and
  `RoyalReserve` directly); `SupervisionSection`, `MemorySection` (`schema.supervision`);
  `SecuritySection` + `TierProfile`, `HoneySection` + `HoneyClearanceSection` + `ClearanceMatrix`
  (`schema.security`, using `waggle.messages.CombShieldLevel`/`HoneyClearance` rather than
  `hivemind.cell.tiers`'s mirror, since `manifest` may not import `hivemind.cell`).
- **Loading** (`hivemind.manifest.loader`): `load_manifest(path, environ=None) -> HiveManifest`.
  Raises `ManifestError` naming the file and, for a validation failure, every field's dotted
  location.
- **Environment** (`hivemind.manifest.env`), the one place `HIVEMIND_*` is read: `EnvOverrides`,
  `read_env(environ) -> EnvOverrides`, `apply_env(manifest, overrides) -> HiveManifest`,
  `provider_api_key(name, spec, environ) -> SecretStr | None`.
- **Errors** (`hivemind.manifest.errors`): `ManifestError(ConfigurationError)`.

### The slot-table validators (`LlmSection`)

Every `[llm.slots.<key>]` key is lowercase snake_case (enforced by the dict key's own field
pattern, not a separate check). Four `@model_validator`s then run: every binding's `provider` must
name a declared `[llm.providers.*]` entry; every `fallback` must name another `[llm.slots]` key,
and following a chain of fallbacks may never revisit a key; every `hivemind.forage.ModelSlot`
member's `manifest_key` must be bound directly (not merely reachable through someone else's
fallback); and `[llm] offline = true` refuses any provider whose `base_url` is empty (hosted) or
whose host is not loopback (`waggle.is_loopback_host`).

### Cross-section checks (`HiveManifest`)

Only one: every `[forage.map.<source_id>].provider` must name a declared `[llm.providers.*]`
entry. A slot's model need not appear anywhere on the Forage map; the map and the slot table are
two independent ways of naming what a call may use.

### `[hive_stand] keep_root` (roadmap step 5.0e)

An optional directory outside `scratch_root` that outlives every lease: the `keep` tool's own
destinations, and the one path class the leave policy (`supervision/defaults/leave-policy.toml`)
`ALLOW`s by default. `None` (the default) means the Hive keeps nothing past a lease's release.
`HiveStandSection`'s own `@model_validator` refuses it at load time, not at the first real run,
in two cases: a `keep_root` that is inside, equal to, or contains `scratch_root` (scratch's own
wholesale removal on release would take it too), and a `keep_root` set while `access_level` is
`READ_ONLY` or `SCRATCH` (the leave policy's hard rule always `DENY`s a leaving at those levels,
so nothing kept there could ever actually stay -- the message says to raise `access_level` to
`"full"`). `hivemind.cell.local.HiveStandConfig.from_section` resolves it the same way it resolves
`scratch_root`, relative to the manifest's own directory.

## How to test this

```bash
uv run --frozen pytest packages/hivemind/tests/unit/manifest
```

Coverage floor is 95% (codingrules section 14.1), since this is a pure core with no I/O beyond a
single `tomllib.load` call:

```bash
COVERAGE_FILE=.coverage.manifest uv run --frozen pytest -p no:cacheprovider --cov=hivemind.manifest \
    --cov-report=term-missing packages/hivemind/tests/unit/manifest
```
