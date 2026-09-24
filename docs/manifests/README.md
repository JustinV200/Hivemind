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
only manifest paths that must exist before a Hive starts (`db`, `secrets_dir` and `scratch_root`
are all created on demand), and every manifest path resolves against the manifest's own
directory, so an example living in `docs/manifests/` has to name `../supervision/...` rather than
take the schema's repo-root-relative default.

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
  Kept honest by a test that round-trips it through `model_dump`/`model_validate`, `[entrance]`
  (roadmap phase 10, ADR-0033) and `[guard]` (ADR-0031) included.

## `[placement]` and `[virtual_cells]` (roadmap step 5.7)

`[placement]` (`prefer`, `allow_hive_stand`, `[placement.roles.<role>]` overrides) and
`[virtual_cells]` (the default shape of a freshly provisioned Virtual Cell, plus a nested
`[virtual_cells.overwinter]` per `docs/adr/0029-overwintering-policy.md`) feed
`hivemind.queen.placement.decide.decide` (`docs/adr/0028-placement-policy-real-versus-virtual.md`).
Both default such that a manifest that omits them entirely behaves exactly as before this step:
`prefer = "real"`, `allow_hive_stand = true`, and `[virtual_cells] backend` unset means no Virtual
side is configured at all, so every task stays on the Real side.

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
| `HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY` | Nothing in the manifest | The Entrance's Web Push VAPID private key: base64url of the raw 32-byte P-256 scalar (what `web-push generate-vapid-keys` prints as the private key). Used instead of the `entrance.vapid` key minted into the secret store; set it to keep browser subscriptions working across a reinstall. A secret: read into a `SecretStr`, never logged (ADR-0034). |
| `HIVEMIND_ENTRANCE_VAPID_SUBJECT` | Nothing in the manifest | The VAPID contact a push service may use: a `mailto:` or `https:` URI (RFC 8292). |

## Secrets

**No provider's API key ever appears in a manifest file.** There is no `api_key` field anywhere in
the schema; `[llm.providers.<name>]` names an `api_key_env` (a variable name, not a value) that
defaults to `HIVEMIND_<NAME>_API_KEY` when omitted (the provider's own manifest key, upper-cased).
`hivemind.manifest.provider_api_key(name, spec, environ)` is the one function that reads a
provider's key, and it returns a `pydantic.SecretStr`, whose `repr` never shows the value, so a key
never lands in a log line or a Pheromone Trail event by accident (codingrules section 13).

**Key material the Hive mints itself lives in the secret store at `[hive] secrets_dir`**
(`hivemind.common.secrets.FileSecretStore`), never in the manifest and never in a `HIVEMIND_*`
variable. The manifest names only the directory (default `secrets`, beside the manifest, resolved
like `db`); the store keeps one file per secret, named after it, written atomically (a temporary
file, flushed, then renamed over the old one):

- `hive.ed25519`: the Hive's own Ed25519 identity key, minted on the first `hive run` with a
  Virtual side configured. The Queen signs every Virtual Cell frame with it, so a Cell that
  outlives a Queen restart still verifies the next Queen. The Entrance signs every webhook it
  delivers with the same key (ADR-0034).
- `console.ed25519`: the Hive Stand console's device key, never stored in the clear: it is sealed
  with AES-256-GCM under a key derived from the operator password (Argon2id, its own salt), so a
  bee that reads the directory holds nothing usable (ADR-0033).
- `entrance.vapid`: the Entrance's Web Push VAPID private key (the raw 32-byte P-256 scalar),
  minted on first use unless `HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY` supplies one. Every browser
  subscription is bound to its public half, so losing it silently ends Web Push to every device
  (ADR-0034).
- `entrance.push_topic`: 32 random bytes keying the Web Push `Topic` header, so a push service
  cannot compute or correlate it (ADR-0034); minted on first use.

On Linux and macOS the directory is `0700` and every file `0600` from the moment it is created;
on Windows those modes cannot be expressed, and the user profile's ACL is what protects it. Back
the directory up like a private key and keep it out of version control. The operator password is
not stored anywhere: the Entrance tables in `[hive] db` hold only its Argon2id hash.

## Model ids and provider URLs

`scripts/check_no_model_ids.py` exempts every path with a `manifest` or `docs` segment, which is
why a real Claude or Ollama model id is allowed to appear in this directory and inside
`hivemind.manifest`'s own tests, but nowhere else in the codebase (codingrules section 8.6).
