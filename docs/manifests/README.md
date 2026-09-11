# Annotated example Hive Manifests

A Hive Manifest is the TOML configuration file that describes one running Hive (a Queen, its
Wardens, and the Cells they supervise): which model providers it may call, how Forage divides
capacity, its escalation policy and memory budgets, and its security posture. It is validated into
`hivemind.manifest.HiveManifest` (pydantic) by `hivemind.manifest.load_manifest`; nothing above
`hivemind.manifest` ever sees the raw TOML dict (codingrules section 9).

## The three example manifests

Every file here is loaded in a test (`packages/hivemind/tests/unit/manifest/test_loader.py`), so
none of them can silently drift from the schema. That test also resolves each one's `[supervision]`
`policy_file` and `capping_tiers_file` and asserts the files are really there: those two are the
only manifest paths that must exist before a Hive starts (`db` and `scratch_root` are both created
on demand), and every manifest path resolves against the manifest's own directory, so an example
living in `docs/manifests/` has to name `../supervision/...` rather than take the schema's
repo-root-relative default.

- **`minimal.toml`** -- the smallest manifest that validates: one hosted provider (Anthropic) and
  every model slot bound to it. Two things have no safe default and must always be supplied even
  in a "minimal" manifest: every `hivemind.forage.ModelSlot` must resolve to a binding
  (`[llm.slots.*]`), and the Drone role must have a Forage footprint (`[forage.roles.drone]`),
  because the Drone is the only Worker role phase 3 implements. It also names the two
  `[supervision]` data files, for the path reason above, which is the one way it is not quite the
  smallest file that validates.
- **`local.toml`** -- one OpenAI-compatible server (e.g. Ollama) on loopback, with
  `[llm] offline = true`. Demonstrates codingrules section 8.6's "offline is a first-class mode":
  every provider's `base_url` must be loopback under `offline = true`, checked with
  `waggle.is_loopback_host`, or the manifest fails to load. Its capability overrides show a weak
  local model's honest shape: no native tool calls, no schema-enforced output, JSON mode only.
- **`full.toml`** -- every section and every field the schema supports, one comment per field.
  Kept honest by a test that round-trips it through `model_dump`/`model_validate`. `[entrance]` is
  not shown: it is added in phase 10.

## `HIVEMIND_*` environment variables

Codingrules section 13: environment variables are read in exactly one place
(`hivemind.manifest.env`), are always prefixed `HIVEMIND_`, and only ever override a value the
manifest file already has a slot for. `hivemind.manifest.load_manifest` applies them when a caller
passes an `environ` mapping; nothing reads `os.environ` implicitly.

| Variable | Overrides | Notes |
|---|---|---|
| `HIVEMIND_DB` | `[hive] db` | A path; resolved the same way as the manifest's own `db` field. |
| `HIVEMIND_LLM_OFFLINE` | `[llm] offline` | Accepts `1`, `true` or `yes` (case-insensitive) as true; any other value the variable is actually set to is an error, not a silent no-op -- there is no way to force it back to `false` through the environment, only by leaving it unset. |
| `HIVEMIND_HIVE_STAND_SCRATCH_ROOT` | `[hive_stand] scratch_root` | A path. |
| `HIVEMIND_LOG_LEVEL` | Nothing in the manifest | Read by the process's own logging setup, not folded into `HiveManifest`; there is no `[logging]` section (yet). |
| `HIVEMIND_ENV` | `[hive] env` | Must be `dev` or `prod`. |

## Secrets

**No provider's API key ever appears in a manifest file.** There is no `api_key` field anywhere in
the schema; `[llm.providers.<name>]` names an `api_key_env` (a variable name, not a value) that
defaults to `HIVEMIND_<NAME>_API_KEY` when omitted (the provider's own manifest key, upper-cased).
`hivemind.manifest.provider_api_key(name, spec, environ)` is the one function that reads a
provider's key, and it returns a `pydantic.SecretStr`, whose `repr` never shows the value, so a key
never lands in a log line or a Pheromone Trail event by accident (codingrules section 13).

## Model ids and provider URLs

`scripts/check_no_model_ids.py` exempts every path with a `manifest` or `docs` segment, which is
why a real Claude or Ollama model id is allowed to appear in this directory and inside
`hivemind.manifest`'s own tests, but nowhere else in the codebase (codingrules section 8.6).
