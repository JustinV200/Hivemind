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
  `ProviderSpec`, `CapabilityOverrides`, `SlotBinding`, `ServerSpec` (`schema.llm`, a sub-package:
  `providers`, `section`, `floors`, `servers`); `ForageSection`
  (`schema.forage`, embedding `hivemind.forage`'s `RoleFootprint`, `ModelSourceSpec` and
  `RoyalReserve` directly); `SupervisionSection` + `ReconnectBudgetSection` + `WatchSection` (on
  `WatchTuning`, its five numbers, which a device manifest shares), `MemorySection`
  (`schema.supervision`; the first two are phase 11's `[supervision.reconnect_budget]` and
  `[supervision.watch]`); `SwarmSection` + `SwarmLadderSection` +
  `SwarmNucSection` (`schema.swarm`, phase 11: the swarm listener, invites, liveness and the
  ladder's thresholds, disabled by default);
  `SecuritySection` + `TierProfile`, `HoneySection` + `HoneyClearanceSection` + `ClearanceMatrix`
  (`schema.security.tiers`, using `waggle.messages.CombShieldLevel`/`HoneyClearance` rather than
  `hivemind.cell.tiers`'s mirror, since `manifest` may not import `hivemind.cell`); `GuardSection`
  and its tables (`schema.security.guard`, roadmap step 10.2). `schema.security` is a sub-package
  whose face re-exports both.
- **Loading** (`hivemind.manifest.loader`): `load_manifest(path, environ=None) -> HiveManifest`.
  Raises `ManifestError` naming the file and, for a validation failure, every field's dotted
  location.
- **The device manifest** (`hivemind.manifest.device`, roadmap steps 11.6a and 11.8):
  `DeviceManifest` and `load_device_manifest(path)`. The small file a colonized Swarm device's
  Warden reads: `[llm]`, in the Hive Manifest's own shape (so every slot-table rule below holds),
  and `[forage.map]`, only the rows the device's own `[llm.servers]` list (the models a Nuc may
  load, copied from the Hive Manifest, each served by its server's provider and naming no host
  Cell). It also holds `[supervision]` (roadmap 11.9a): `max_offline_s` and
  `[supervision.reconnect_budget]`, the two figures the device's Warden acts on when its link is
  cut, checked by the rule the Hive's own `[supervision]` uses (`require_budget_inside_offline_limit`:
  the budget is carved out of the offline limit) and defaulting to the Hive's shipped values when the
  table is absent; and `[supervision.watch]` (roadmap 11.10, `DeviceWatchSection`): the five
  numbers its Warden watches its machine by, with no `roots` (refused with the reason: the operator
  names a device's directories with `hive swarm watch`). Any other section, or any other
  `[supervision]` key, is refused.
  `docs/manifests/device.toml` is the annotated example.
- **Environment** (`hivemind.manifest.env`), the one place `HIVEMIND_*` is read: `EnvOverrides`,
  `read_env(environ) -> EnvOverrides`, `apply_env(manifest, overrides) -> HiveManifest`,
  `provider_api_key(name, spec, environ) -> SecretStr | None`, and `InCellEnv`/`read_in_cell_env`
  for the in-Cell Warden, which since step 11.6a also carries the seven variables the Pollen
  Packet gives a colonized device's Warden (`HIVEMIND_CELL_LEASE=borrowed`, `HIVEMIND_WARDEN_ID`,
  `HIVEMIND_NODE_ID`, `HIVEMIND_ACCESS_LEVEL`, `HIVEMIND_STATE_DIR`, `HIVEMIND_TLS_PIN`,
  `HIVEMIND_DEVICE_MANIFEST`) and, since step 11.10, an eighth that is set only when the operator
  named directories for it to watch (`HIVEMIND_WATCH_ROOTS`, a JSON array).
- **Errors** (`hivemind.manifest.errors`): `ManifestError(ConfigurationError)`.
- **Advice** (`hivemind.manifest.advice`): `judge_shares_ripener(llm) -> str | None`, a warning
  when the JUDGE and RIPENER slots resolve to the same model (same model id at the same provider
  kind and base URL). A valid manifest is never refused for it: `build_honey_access` logs it
  as `manifest.judge_shares_ripener` whenever it builds a judge that will run, so `hive run`,
  `hive serve` and the `hive honey` commands that open the store with its models (`ripen --now`,
  `review --judge`, `query`, `reembed`) all say it on stderr. The clearance judge (ADR-0038) is meant to be an independent
  second reading; on the Ripener's own model it shares its blind spots, and a weak shared model
  blocks every lowering.

### The slot-table validators (`LlmSection`)

Every `[llm.slots.<key>]` key is lowercase snake_case (enforced by the dict key's own field
pattern, not a separate check). Four `@model_validator`s then run: every binding's `provider` must
name a declared `[llm.providers.*]` entry; every `fallback` must name another `[llm.slots]` key,
and following a chain of fallbacks may never revisit a key; every `hivemind.forage.ModelSlot`
member's `manifest_key` must be bound directly (not merely reachable through someone else's
fallback); and `[llm] offline = true` refuses any provider whose `base_url` is empty (hosted) or
whose host is not loopback (`waggle.is_loopback_host`).

`[llm.floors]` (roadmap step 8.1, `schema/llm/floors.py`) maps a slot's lowercase name to its minimum
model grade, 1 to 5; a key that is not a `ModelSlot`'s manifest key is rejected, and
`LlmSection.grade_floors()` returns a floor for every slot with the shipped defaults (queen 5,
attendant, ripener, embedder and transcriber 1, others 2) filling any the table omits.

`[llm.servers.<server_id>]` (roadmap step 8.3, `schema/llm/servers.py`) declares a model server a
Cell's Warden may start: `LlmSection` requires its provider to exist, to be `openai_compat` and to
sit on a loopback `base_url` carrying the server's own port. `argv` may name only the placeholders
`{port}` and `{scratch}`.

### Cross-section checks (`HiveManifest`)

Two: every `[forage.map.<source_id>].provider` must name a declared `[llm.providers.*]` entry, and
every `[llm.servers.<server_id>].sources` entry must name a `[forage.map]` row served by that
server's own provider. A slot's model need not appear anywhere on the Forage map; the map and the
slot table are two independent ways of naming what a call may use.

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
