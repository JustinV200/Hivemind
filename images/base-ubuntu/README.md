# base-ubuntu image

Terminal-first Virtual Cell image on Ubuntu 24.04 LTS: a shell, Python, the `hivemind` runtime
and a Waggle client that connects **out** to the Queen, and nothing else (codingrules section
8.7: "Terminal first, peripherals on demand"; the Exoskeleton bundle lives in `desktop-ubuntu`,
built on top of this one). Every other image (`desktop-ubuntu`, `night-veil-ubuntu`) builds on
this one. This is roadmap step 5.3; the in-Cell session and Warden spawn strategy it boots into
are roadmap step 5.5.

## What the image's entry point does

The image's `ENTRYPOINT` is `hivemind-in-cell`, the console script for
`hivemind.cli.in_cell.main:main` (`packages/hivemind/pyproject.toml`). On boot it:

1. Reads its configuration from `HIVEMIND_*` environment variables (the table below), the one
   place they are read (`hivemind.manifest.env.read_in_cell_env`, codingrules section 13).
2. Probes this container's own platform, capabilities and Forage capacity (the same stdlib-only
   probe `hivemind.cell.local.probe.probe_host` uses for the Hive Stand -- a Virtual Cell image is
   Ubuntu Linux too) and builds the one `Cell` it is (`hivemind.wardens.spawn.in_cell.
   InCellSpawnSource`, the `in_cell` Warden spawn strategy).
3. Dials **out** to `HIVEMIND_QUEEN_WAGGLE_URL` over a signed WebSocket connection -- this image
   exposes no inbound port, so it has to be the one that connects.
4. Sends one signed `CellReady` announcing itself, then a `CapacityReport` naming its probed
   `ForageCapacity`, then one `CellHeartbeat` -- the three frames `hivemind.queen.cell_gate.
   listener.CellListener`'s own readiness gate waits for before a real Warden ever exists
   (`hivemind.cli.in_cell.link`).
5. Builds and starts a genuine, task-executing `hivemind.wardens.warden.Warden` over that same
   connection (`hivemind.cli.in_cell.deps.build_in_cell_warden_deps`): it leases the one Cell it
   is, spawns sub-bees under whatever `GrantIssued`/`TaskAssign` the Queen sends, runs their
   acceptance and reports `TaskProgress`/`TaskResult` back, and periodically ships its own local
   Pheromone Trail segment to the Queen as `TrailSegmentSync` (`hivemind.wardens.trail_sync`) --
   this Cell's trail store lives and dies with the container, so that segment is this Cell's only
   way of getting its own `warden.*`/`cell.*`/`task.*`/`llm.*` history onto the Hive's audit log.
   Model access from inside a Cell (`hivemind.cli.in_cell.providers.
   build_in_cell_provider_registry`) resolves every slot against the operator's own
   `[llm.providers]`/`[llm.slots]` table when the Queen provisioned one (`HIVEMIND_PROVIDERS`/
   `HIVEMIND_SLOTS` below), or falls back to a scriptable fake with nothing reachable when it did
   not -- see the variable table below and "Not yet in this image".
6. Runs until a signed `Shutdown` or `CellTeardownRequest` arrives from the Queen, then stops --
   every sub-bee reaped, its lease released, a final best-effort trail sync -- and the container
   exits. Destroying the container (not a graceful in-container cleanup) is the Undertaker's
   actual teardown of a Virtual Cell -- see `hivemind.cell.in_cell`'s own module docstring for why
   nothing here tries to leave anything "restored".

See `hivemind.cli.in_cell`'s own package docstring for the full composition-root detail, and this
dispatch's own report (roadmap step 5.3) for exactly what the Queen side still needs before it
actually reads the `TrailSegmentSync` chunks this image already sends.

## Layers, in order

| # | Layer | Why |
|---|---|---|
| 1 | `ubuntu:24.04` (both stages) | codingrules section 2: every Virtual Cell image is Ubuntu LTS. |
| 2 | `ca-certificates`, `python3`, `python3-venv` (builder only) | TLS for PyPI, and the interpreter uv's venv wraps. Ubuntu 24.04's default `python3` is 3.12 (codingrules section 2: Python 3.12+). |
| 3 | `uv` (at least 0.8.17), bootstrapped from PyPI into a throwaway venv (builder only) | The one tool that installs the Hive's own dependencies from `uv.lock` (codingrules section 2). It replaced `curl ... \| sh`, whose pipeline exits 0 when the download fails, so a blocked or broken download surfaced only much later as "uv: not found". |
| 4 | The workspace lockfile (`uv.lock`) and every member's own `pyproject.toml` | uv needs every `[tool.uv.workspace] members` entry on disk to resolve the workspace graph, even though only `hivemind`'s own closure gets installed. |
| 5 | The `waggle` and `hivemind` source trees only | Not `pollen` (a different device's own connector) and not `packages/observation-web` (the browser front end): neither belongs inside a Virtual Cell. |
| 6 | `uv sync --frozen --no-dev --no-editable --package hivemind` | Installs `hivemind` and its own dependency closure (`waggle`, transitively, via `packages/hivemind/pyproject.toml`'s own `dependencies`). `--frozen`: fail rather than silently re-resolve. `--no-dev`: never ruff/mypy/pytest/import-linter inside a shipped image. `--no-editable`: a built distribution, not a path-reference shim back into the builder stage's own source tree (the runtime stage copies only the resulting virtual environment). |
| 7 | `ca-certificates`, `python3` (runtime stage) | Only what the installed venv needs to run; no compiler, no uv, no pip in the shipped image. |
| 8 | A non-root `hive` user, home directory and `/var/lib/hivemind/scratch` | Codingrules section 15: least privilege; nothing runs as root once installed. `/var/lib/hivemind/scratch` is `hivemind.cli.in_cell.config.DEFAULT_SCRATCH_ROOT`, where `InCellSpawnSource` creates each lease's own scratch subdirectory. |
| 9 | The builder stage's venv, built at `/opt/hivemind/venv` (`UV_PROJECT_ENVIRONMENT`) so its console scripts' absolute shebangs are right in the shipped image, copied in and put on `PATH` | The only thing carried from the builder stage into the shipped image. |
| 10 | No `EXPOSE` | Codingrules section 15: a Virtual Cell opens no inbound port. Every Waggle link is dialled **out**. |
| 11 | `ENTRYPOINT ["hivemind-in-cell"]` | The in-Cell Warden entry point (roadmap step 5.5), described above. |

## Build context

The Dockerfile expects to be built with the **repository root** as its build context (not
`images/base-ubuntu/`), because it needs the uv workspace's shared lockfile and every member's own
`pyproject.toml`:

```sh
docker build -f images/base-ubuntu/Dockerfile -t hivemind/base-ubuntu:dev .
```

A `.dockerignore` at the repository root (see the one this dispatch added) keeps the build context
small: it excludes `.venv/`, `.git/`, `node_modules/`, `docs/`, every `tests/` tree, and the other
two `images/*/` directories, none of which this image's `COPY` instructions ever reference.

## Runtime configuration (`HIVEMIND_*` environment variables)

Read once, by `hivemind.manifest.env.read_in_cell_env`, and validated by
`hivemind.cli.in_cell.config.build_runtime_config` (codingrules section 13: environment variables
are read in exactly one place). No model id or provider URL is ever hard-coded here or anywhere
else in this image (codingrules section 8.6): `HIVEMIND_PROVIDERS`/`HIVEMIND_SLOTS` below are a
*runtime* copy of the operator's own `[llm.providers]`/`[llm.slots]` table, handed to this one
Cell by the Queen's own composition root (`hivemind.cli.compose.virtual_cell_providers`) at
provision time -- code in this image still never names a model or vendor itself.

| Variable | Required | Meaning |
|---|---|---|
| `HIVEMIND_QUEEN_WAGGLE_URL` | yes | Where this Cell dials out to (`wss://` anywhere, `ws://` on loopback only -- `waggle.uris.check_waggle_uri`). |
| `HIVEMIND_CELL_ID` | yes | The `cell_<ULID>` id the Queen minted for this Cell when it provisioned it. |
| `HIVEMIND_HIVE_ID` | yes | The Queen's own bee address (`hive_<ULID>`), the `recipient` of every envelope this Cell sends. |
| `HIVEMIND_QUEEN_NODE_ID` | yes | The `node_<ULID>` the Queen signs its own frames as, so this Cell's Verifier knows whose signature to check (signing is mandatory across a machine boundary, roadmap step 1.7). |
| `HIVEMIND_CELL_SIGNING_KEY` | one of these two | This Cell's own Ed25519 private key, hex-encoded (the Queen provisions it into the container at create time). |
| `HIVEMIND_CELL_SIGNING_KEY_FILE` | one of these two | A file holding the same hex text, for a mounted secret instead of a bare environment variable (preferred when both are set, codingrules section 15). |
| `HIVEMIND_QUEEN_VERIFY_KEY` | one of these two | The Queen's own Ed25519 public key, hex-encoded. |
| `HIVEMIND_QUEEN_VERIFY_KEY_FILE` | one of these two | A file holding the same hex text (preferred when both are set). |
| `HIVEMIND_SOCKS_PROXY_URL` | no | A SOCKS proxy Waggle should dial through. Carried, not yet acted on -- Night Veil (roadmap step 5.7a) is what routes this over Tor; a `base-ubuntu` Cell never sets it. |
| `HIVEMIND_PROVIDERS` | no | This Hive's own `[llm.providers]` table, as a bounded JSON array (`hivemind.hive.backends.provider_table.render_providers_json`): one object per provider, each naming its `kind`, its own Cell-reachable `base_url` (already rewritten from the Hive Stand's own loopback address), `default_model`, capability overrides and the `api_key_env` variable name its own key (if any) rides under. Absent means this Cell resolves every model slot to a scriptable fake instead (`hivemind.cli.in_cell.providers`). |
| `HIVEMIND_SLOTS` | no | This Hive's own `[llm.slots]` table, as a bounded JSON array (`render_slots_json`); present exactly when `HIVEMIND_PROVIDERS` is. |
| `HIVEMIND_<NAME>_API_KEY` | no | One such variable per provider named in `HIVEMIND_PROVIDERS` that actually has a key configured (e.g. `HIVEMIND_ANTHROPIC_API_KEY`) -- the exact name `[llm.providers.<name>].api_key_env` derives, never the JSON above (a key VALUE never rides that blob). |
| `HIVEMIND_LLM_OFFLINE` | no | Mirrors the Hive Stand's own `[llm] offline` flag; `"true"` when set, absent otherwise. A gateway-alias `base_url` (`host.docker.internal`, QEMU's `10.0.2.2`) is never treated as "non-local" for this check, since it IS the Hive Stand's own machine from inside the Cell. |
| `HIVEMIND_LOG_LEVEL` | no | The structured-logging level (`hivemind.common.logging`); `INFO` if unset. |
| `HIVEMIND_SCRATCH_ROOT` | no | Where the Warden creates each lease's own scratch directory; `/var/lib/hivemind/scratch` (layer 8) if unset. A real container never sets it; a test or an in-process Cell on a host that cannot create that path (Linux CI) does. |

## Building and testing this image

Docker is not available in the environment that authored this Dockerfile; it has been written
carefully and reviewed by reading, never built. The integration workflow that a Docker-enabled CI
runner adds (a later roadmap step) must, at minimum:

- Build the image from the repository root as build context, exactly as shown above.
- Run it with every required `HIVEMIND_*` variable set against a loopback `WebSocketServer` test
  double (the same shape `packages/hivemind/tests/unit/cli/in_cell/test_main.py` already drives
  in process, without a container) and confirm it sends a signed `CellReady`, then a
  `CapacityReport`, then a `CellHeartbeat`, runs a real Warden, and stops cleanly (every sub-bee
  reaped) on a `Shutdown`.
- Confirm the image runs as the non-root `hive` user (`docker run ... whoami` prints `hive`).
- Confirm no port is published or listening inside the container (`docker inspect` shows no
  `ExposedPorts`; nothing binds a socket at boot).
- Confirm `scripts/check_no_model_ids.py` and the other `scripts/check_*.py` hygiene gates pass
  against this directory (they already do as authored; this is a regression check for future
  edits).

## Not yet in this image

- **Night Veil attestation** (VPN, Tor, kill-switch rules) is `night-veil-ubuntu`, built on
  `desktop-ubuntu` (roadmap step 5.3a), not this image.
- **The Exoskeleton bundle** (Xvfb, xdotool, PulseAudio, a browser) is `desktop-ubuntu` (roadmap
  step 6.1). This image is terminal-only by design.
- **A real model provider** now reaches this image (roadmap step 8.x closed the gap this bullet
  used to describe): the Queen's own composition root (`hivemind.cli.compose.
  virtual_cell_providers`) hands a provisioned Cell its own `[llm.providers]`/`[llm.slots]` table
  via `HIVEMIND_PROVIDERS`/`HIVEMIND_SLOTS` (table above), and `hivemind.cli.in_cell.providers.
  build_in_cell_provider_registry` builds a real `ProviderRegistry` from it. What is still open:
  `cli/compose/hive.py`'s own call to `build_virtual_cells` does not yet pass its `environ`
  argument through, so a provider that needs an API key gets no `HIVEMIND_<NAME>_API_KEY` forwarded
  to the Cell until that one-line wiring lands (a local server with no auth configured is
  unaffected).
- **The Queen actually reading `TrailSegmentSync`.** This image's Warden already ships its own
  trail segment (`hivemind.wardens.trail_sync`) and `hivemind.queen.trail_sync.
  TrailSegmentReceiver` already reassembles and merges it, but nothing in `hivemind.queen` calls
  that receiver yet -- see this dispatch's own report for exactly which drain loop should own it.
