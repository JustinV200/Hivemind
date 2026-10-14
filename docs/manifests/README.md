# Annotated example Hive Manifests

A Hive Manifest is the TOML configuration file that describes one running Hive (a Queen, its
Wardens, and the Cells they supervise): which model providers it may call, how Forage divides
capacity, its escalation policy and memory budgets, and its security posture. It is validated into
`hivemind.manifest.HiveManifest` (pydantic) by `hivemind.manifest.load_manifest`; nothing above
`hivemind.manifest` ever sees the raw TOML dict (codingrules section 9).

## The example manifests

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
- **`offline.toml`** -- a Hive with no external network (roadmap step 8.6, ADR-0045), the file the
  phase 8 exit criteria run against. `[llm] offline = true`; one `openai_compat` provider on a
  loopback `base_url` serves every chat slot; the embedder runs on an in-process
  `sentence_transformers` provider and the transcriber on an in-process `whisper_local` one, neither
  of which names a `base_url`; each model has a `[forage.map]` entry with no `cost` (self-hosted
  is free per token) and its `vram_bytes_required`. It binds the Judge to a larger model than the
  Ripener's, for the reason in ADR-0038 and `docs/runbooks/moving-a-slot-to-local.md`. Where
  `local.toml` is the smallest offline file, this is the complete one. The two in-process models
  must already be in the local cache before the Hive goes dark: offline mode never downloads them.
  The end-to-end suite `packages/hivemind/tests/e2e/offline/` proves it rather than assuming it:
  each case loads this file, points the chat provider at a scripted OpenAI-compatible server on
  loopback (turning the two in-process providers into `openai_compat` rows on the same server, and
  every model id into a scripted one, because the container the suite runs in has no weights),
  keeps `offline = true`, the ids, the slot wiring and the `[forage.map]` grades, runs the phase 8
  scenarios under a guard that fails any socket connect to a non-loopback address, and asserts
  every `llm.call` on the trail names a provider that runs locally. A `local_llm` job runs the same
  claims against a real server (`packages/hivemind/tests/evals/offline/`), so an edit to this file
  reaches both.
- **`full.toml`** -- every section and every field the schema supports, one comment per field.
  Kept honest by a test that round-trips it through `model_dump`/`model_validate`, `[entrance]`
  (roadmap phase 10, ADR-0041) and `[guard]` (ADR-0039) included.
- **`device.toml`** -- not a Hive Manifest: the file the Warden on a colonized Swarm device reads
  (roadmap steps 11.6a and 11.8, ADR-0052), recorded with `pollen install --warden-manifest`. It is
  loaded by `hivemind.manifest.load_device_manifest`, not `load_manifest`, and refuses any section
  but `[llm]` and `[forage.map]`. The model providers it names are the ones the Hive's grants name,
  resolved to addresses this device can reach; a declared `[llm.servers.*]` row is reported to the
  Queen (not started) so she can later promote the device to a Nuc, and the `[forage.map]` rows
  beside it, copied from the Hive's manifest, describe the models that server may load. The Hive's
  own manifest must list the same server id in `[llm.servers]` (with `load_on_start = false`) for
  the Queen to promote the device. API keys come from the device's own environment, as everywhere
  else. A device manifest has no `[supervision]`, so its Warden's offline limit and its budget for
  sending a Drone to find out why its link went (roadmap 11.9a, ADR-0056) are the shipped defaults
  (`max_offline_s` and `[supervision.reconnect_budget]`), and the Drone runs on a model the device
  serves itself, never on one that needs the Queen.

## The transcriber and `kind = "whisper_local"` (roadmap step 6.5a)

`[llm.slots.transcriber]` binds `ModelSlot.TRANSCRIBER`, the slot that hears (audio in, text out,
`docs/adr/0033-transcription-provider-whisper-first.md`), like every other slot. Two provider
kinds can serve it: `openai_compat` (any hosted Whisper API or local speech server speaking
`/audio/transcriptions`) and `whisper_local`, Whisper in the Hive's own process through the
optional `hivemind[whisper]` extra. A `whisper_local` provider takes no `base_url` (the schema
refuses one: it reaches no server), is accepted by `offline = true` as it is, never downloads
weights while offline, and hears one clip at a time, so give it `seats = 1`. An `anthropic`
provider cannot transcribe: binding the slot to one loads, but the registry refuses it at first
use. `full.toml` shows a commented-out `whisper_local` provider; the model sizes the examples
recommend (`large-v3-turbo` with a GPU, `small` on a CPU) stand until the eval harness measures
them. `[llm.providers.<name>.capabilities] audio` says whether a chat provider takes audio parts
directly; every shipped chat adapter but the fake leaves it off.

## `[llm.servers]` (roadmap step 8.3)

`[llm.servers.<server_id>]` declares a model server a Cell's Warden may start on its own Cell
(`docs/adr/0044-local-model-serving-via-openai-compatible-api.md`): `provider` (the
`[llm.providers]` row it answers as, which must be `openai_compat` on a loopback `base_url` whose
port equals the server's `port`), `argv` (the command, with the literal `{port}` and `{scratch}`
substituted; the program name lives here and nowhere in code), `port`, `health_path`,
`startup_timeout_s`, `sources` (the `[forage.map]` entries it may load, each served by that same
provider), `keep_alive_s` and `load_on_start`. A Cell that reports `can_host_model` starts the
server once it holds its lease, waits for the provider's health probe, and adds each source to the
live Forage map within the Queen's ceilings, telling her with `forage.source_added`; stopping the
Warden stops the server and removes the sources (`forage.source_removed`). The Hive Stand reports
`can_host_model` exactly when `[llm.servers]` is non-empty. `full.toml` shows one annotated server.

## `[placement]` and `[virtual_cells]` (roadmap step 5.7)

`[placement]` (`prefer`, `allow_hive_stand`, `[placement.roles.<role>]` overrides) and
`[virtual_cells]` (the default shape of a freshly provisioned Virtual Cell, plus a nested
`[virtual_cells.overwinter]` per `docs/adr/0029-overwintering-policy.md`) feed
`hivemind.queen.placement.decide.decide` (`docs/adr/0028-placement-policy-real-versus-virtual.md`).
Both default such that a manifest that omits them entirely behaves exactly as before this step:
`prefer = "real"`, `allow_hive_stand = true`, and `[virtual_cells] backend` unset means no Virtual
side is configured at all, so every task stays on the Real side.

Roadmap step 6.12 adds `[virtual_cells] exoskeleton_image` (default `"desktop-ubuntu"`): the image
a Virtual Cell boots when its task needs an Exoskeleton (a display, input, audio or browser
attachment, `docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md`). `default_image`
stays the terminal-only image every other task boots, so `images/base-ubuntu` never has to carry
Xvfb, PulseAudio or Chromium. Name the tag you built, as for `default_image` (for example
`"hivemind/desktop-ubuntu:dev"`). A Real Cell still takes an Exoskeleton task first when it can:
a browser-only task where it has a browser and its access level is `"SCRATCH"` or `"FULL"`, a
desktop task where it can start a display at `"FULL"`, or where `[hive_stand]
exoskeleton_real_display = true` lends the operator's own running display at `"FULL"`; a task that
needs audio also needs the Cell's sound tools. Every Real Cell placement skips is named in the
placement reason with what it lacks.

## `[exoskeleton]` (roadmap steps 6.4-6.6)

`[exoskeleton]` tunes the Exoskeleton (the optional display, input, audio and browser a task may
ask for on its Cell, `docs/adr/0031-exoskeleton-on-x11-with-playwright-fast-path.md`) on the Hive
Stand's own Warden: `screen_width` and `screen_height` (default 1280x800, each 320 to 8192) size a
display attach starts and the browser window in it, and `ready_timeout_s` (default 30) bounds how
long attach waits for everything it started. `recording_retention_days` (default 30) is how long
the flight recorder keeps a recording after its last action
(`docs/adr/0032-gui-actions-are-capped-recorded-and-rolled-back-by-checkpoint.md`): recordings live
in two tables of the `[hive] db` file, and the sweep that deletes older ones runs as the Hive
starts. `hive recordings list|show|export` reads them; `export` writes a self-contained HTML page
(frames inline, no script) and its JSON summary. Whether Chromium keeps its own sandbox is not a
setting: the Hive keeps it on, except inside a Virtual Cell (the Cell is the sandbox) and on a Hive
Stand running as root, where Chromium cannot start with it. A Virtual Cell reads no manifest, so
its in-Cell Warden uses these defaults and keeps its recordings in memory, with the Cell.

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
| `HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY` | Nothing in the manifest | The Entrance's Web Push VAPID private key: base64url of the raw 32-byte P-256 scalar (what `web-push generate-vapid-keys` prints as the private key). Used instead of the `entrance.vapid` key minted into the secret store; set it to keep browser subscriptions working across a reinstall. A secret: read into a `SecretStr`, never logged (ADR-0042). |
| `HIVEMIND_ENTRANCE_VAPID_SUBJECT` | Nothing in the manifest | The VAPID contact a push service may use: a `mailto:` or `https:` URI (RFC 8292). |
| `HIVEMIND_ENTRANCE_TUNNEL_<NAME>` | Nothing in the manifest | Handed to the tunnel client the Entrance runs in `expose = "tunnel"` mode, as `<NAME>`: the prefix is removed, so the client finds the variable it documents (`HIVEMIND_ENTRANCE_TUNNEL_TUNNEL_TOKEN` reaches cloudflared as `TUNNEL_TOKEN`). No shell expands variables in `[entrance] tunnel_command`, so this is how a token reaches the client without appearing in its argv or the manifest (ADR-0041). Any number may be set; the bare prefix with no name is an error. Secrets: read into `SecretStr`, never logged. The client's environment is the Hive's own minus every other `HIVEMIND_*` variable, plus these. |

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
  delivers with the same key (ADR-0042).
- `console.ed25519`: the Hive Stand console's device key, never stored in the clear: it is sealed
  with AES-256-GCM under a key derived from the operator password (Argon2id, its own salt), so a
  bee that reads the directory holds nothing usable (ADR-0041).
- `entrance.vapid`: the Entrance's Web Push VAPID private key (the raw 32-byte P-256 scalar),
  minted on first use unless `HIVEMIND_ENTRANCE_VAPID_PRIVATE_KEY` supplies one. Every browser
  subscription is bound to its public half, so losing it silently ends Web Push to every device
  (ADR-0042).
- `entrance.push_topic`: 32 random bytes keying the Web Push `Topic` header, so a push service
  cannot compute or correlate it (ADR-0042); minted on first use.
- `entrance.ca_key` and `entrance.ca_cert`: the Hive's own certificate authority for mutual TLS
  (ADR-0041): an EC P-256 private key (PKCS#8 DER) and its self-signed CA certificate (PEM, ten
  years, `path_length=0`). It signs every device's client certificate (90 days) for the remote
  listener in `lan` and `tunnel` mode, and in `vpn` mode with `mutual_tls = true`. Minted on first
  use; never replaced: losing the key means minting a new authority (remove both) and re-issuing
  every device's certificate. A lost certificate alone is re-derived from the key.
- `<name>.ed25519`: a named node key minted by `hive keys create NAME` for a peer to pin. `hive
  keys list` shows every Ed25519 key here by its public half (hex, the form a peer pins) and its
  fingerprint, never the private half; `hive keys revoke NAME` deletes one, and refuses
  `hive.ed25519` (moved only by Supersedure) and `console.ed25519` (replaced only by `hive entrance
  operator password --reset`).

On Linux and macOS the directory is `0700` and every file `0600` from the moment it is created;
on Windows those modes cannot be expressed, and the user profile's ACL is what protects it. Back
the directory up like a private key and keep it out of version control. The operator password is
not stored anywhere: the Entrance tables in `[hive] db` hold only its Argon2id hash.

Beside `[hive] db`, `hive serve` keeps two small files of its own: `<db>.serve.lock`, an
operating-system lock it holds for its whole life (so a second `hive serve`, `hive entrance
operator password --reset` and `hive entrance unlock --console` refuse while it runs), and
`<db>.serve.json`, where its loopback listener answers (pid, host, port), which the Hive Stand's
`hive entrance` commands read to reach it. A laptop's own device key never lives in any Hive's
secret store: `hive remote enrol` keeps it, with the profile naming its Hive, under the laptop
user's config directory (`<config>/hivemind/remote/`, owner-only).

## Model ids and provider URLs

`scripts/check_no_model_ids.py` exempts every path with a `manifest` or `docs` segment, which is
why a real Claude or Ollama model id is allowed to appear in this directory and inside
`hivemind.manifest`'s own tests, but nowhere else in the codebase (codingrules section 8.6).

## `[royal_jelly]` and `[basket]` (roadmap phase 9)

`[royal_jelly]` (with `[royal_jelly.limits]`) configures the Royal Jelly Lab, which scaffolds a new
tool and proves it in the Quarantine Comb before the Comb Registry keeps it
(`docs/adr/0046-tool-spec-comb-registry-and-scopes.md`,
`docs/adr/0047-quarantine-comb-gates-and-sandbox.md`). `sandbox = "virtual"` (the default) runs the
Comb in a disposable Virtual Cell with no network and needs `[virtual_cells] backend`; writing it
explicitly in a manifest with no backend fails to load. A manifest that never mentions the Lab and
sets no backend still loads, and its Lab simply has no sandbox to promote with. `sandbox =
"subprocess"` runs the Comb in a local process of the Hive Stand: development only, and the Hive
logs a warning at start. `[basket]` configures the Basket, the store of signed, inert blobs
(`docs/adr/0048-basket-inert-blobs-residency-and-transfer.md`): where bytes live (`root`, default
a `basket` directory beside `[hive] db`), the largest blob, the shipment chunk size (at most
262144 bytes, the wire's bound), the resident disk ceiling a Cell gets when Forage sets none, and
whether a promoted tool is shipped at once. Both sections have a default for every key, so neither
needs to appear in a manifest; `full.toml` shows every key. Neither holds a secret: the key that
signs blobs is the Hive key in the secret store.

## `[swarm]` and the `[supervision]` additions (roadmap phase 11)

`[swarm]` configures the Swarm, the mesh of devices running a Pollen Packet
(`docs/adr/0051-pollen-packet-is-a-gateway-and-the-trust-model.md`). It is off by default
(`enabled = false`) and loopback-only (`listen_host = "127.0.0.1"`), so a manifest that never
mentions it has no listener and no devices. It names the swarm listener the packets dial
(`listen_host`, `listen_port`) and the URL a device is told to dial in its invite
(`advertise_url`, empty to derive it); any `listen_host` that is not a loopback address serves
`wss://`, so it needs both `tls_cert` and `tls_key` (both or neither; a self-signed certificate is
fine, because an invite carries its fingerprint and the packet pins that). It sets the invite's
lifetime (`invite_ttl_minutes`), how a device's liveness is judged (`heartbeat_interval_s`,
`heartbeat_miss_limit`, the dead-man switch's `dead_man_s` and the Queen's `unreachable_grace_s`)
and the access level an invite gets when `hive swarm invite` names none (`default_access`:
`READ_ONLY`, `SCRATCH` or `FULL`, spelled like `[hive_stand] access_level`). `[swarm.ladder]` and
`[swarm.nuc]` hold the thresholds that decide when moving a device's Warden onto it, or giving it
a model server, is worth doing. `[supervision]` gains two sub-tables: `[supervision.reconnect_
budget]` (diagnostic Drones a Warden may send, and for how long, to find out why its link was
lost; its `wall_clock_s` must be below `max_offline_s`, so a manifest that lowers
`max_offline_s` to 300 seconds or less must lower the budget with it) and `[supervision.watch]`
(the sampling interval, retention, Patrol interval and alarm thresholds of a Real Cell's watch
mode, ADR-0054). Neither section holds a secret; `full.toml` shows every key.
