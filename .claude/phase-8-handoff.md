# Phase 8 handoff (local models and provider routing): state, map, how to run it, open items

> For an agent starting with a clean context. Roadmap phase 8 (steps 8.1 to 8.9) was built on
> 2026-10-11 on branch `ccr-0235b6d7-okyhmn` in a Linux cloud container with no network to any
> model hub and no Docker daemon. Every step that can be proved without a real local model was
> proved here against `hivemind.llm.fake.FakeLLMProvider` or the scripted OpenAI-compatible server
> on loopback (`packages/hivemind/tests/e2e/scripted_openai.py`, a real `openai_compat` adapter
> over real sockets). Everything that needs a real local model is gated `local_llm`, skips cleanly
> here, and is the operator's to run: the `local_llm` job in `.github/workflows/integration.yml`
> is the script for it. **The branch is pushed but not merged, and it has no PR.**

Read these first:

- `CLAUDE.md` and `.claude/codingrules.md`.
- `.claude/roadmap.md` phase 8. Each ticked step carries an "as built" note naming what differs
  from the step text.
- ADR-0044 and ADR-0045 in `docs/adr/`.
- `docs/runbooks/moving-a-slot-to-local.md`.
- The READMEs of `hivemind/llm` (routing, prompts, registry, providers/ollama),
  `hivemind/wardens/local_pool`, `hivemind/wardens/spawn`, `hivemind/queen/forage` and
  `hivemind/cli` (the `llm eval` group).

## 1. Where things stand

- **The roadmap.** 8.1, 8.2, 8.3, 8.5, 8.8 and 8.9 are ticked. 8.4 is half done: the overlay
  mechanism exists and one overlay ships, but the pass itself (every prompt snapshot and the
  phase 3 scenarios against two real local models of different sizes) needs a model server this
  container cannot reach. 8.6 and 8.7 have their e2e proofs over the scripted loopback server
  (see section 4) and their real-model halves in the `local_llm` job. Step 6.13 (Pheromone Mask
  tactics) stays unticked from phase 6 because its text asks the operator for the writing
  instructions before it is built.
- **The exit criteria.** Bullet 1 (the haiku goal, ripening, the browser Forager scenario and a
  Clustering round trip with `offline.toml`, every `llm.call` on a local provider) is proved
  here with the scripted server standing in for the local model, under a socket guard that fails
  any connect to a non-loopback address. Bullet 3 (switching a slot changes only the manifest;
  `src/` checksum identical) is proved the same way. Bullet 2 (`docs/evals/` holds a table
  comparing Claude and two local models across every slot) cannot be produced here: `hive llm
  eval` writes that table, and the `local_llm` job runs it; the operator must run the job (or
  the runbook's commands) on a machine with Ollama and a hosted key to produce the first real
  report.
- **Waggle** is 1.11: `forage.source_added`, `forage.source_removed`, `forage.source_evict` and
  the enum `SourceRemovalCause`.
- **Migrations.** One new one, in the Queen's ledger: `queen/forage/ledger/
  0003_add_measured_grades.sql` (`forage_ledger_measured_grades`). The ledger's migration
  version is now 3.
- **Dependencies.** None new. The `browser` extra (Playwright) was synced in this container to
  run the Forager cases; `uv.lock` is unchanged.

## 2. What phase 8 implemented (map)

Paths are relative to `packages/hivemind/src/hivemind/` unless they say otherwise.

| Step | Modules | Notes |
|---|---|---|
| 8.1 pure | `llm/routing/` (`rules`, `inputs`, `decision`, `eligibility`, `effort`, `policy`), `forage/models/sources.py` (`measured_grade`, `effective_grade`), `forage/map.py` (`add_source`, `remove_source`), `manifest/schema/llm/floors.py`, `cli/stores.py` (`routing_rules`, `build_forage_map(measured_grades=)`) | `choose_binding(RoutingInputs) -> RoutingDecision`. Eligible: grade at or above the slot's floor (raised by HIGH/CRITICAL tempo and by `raised_floor`, capped at the best grade on the map), capabilities present, measured latency within the budget, free or within the spend cap, local when offline. Rank: known before unknown, seated before starved, hosting-plan position, local to the bee's Cell, price, latency, manifest order. Effort: CRITICAL gives HIGH, LOW bar gives LOW, a budget under 10 s gives LOW, else the row's own. Every strength read in allocation, spill, hosting plans and routing uses `effective_grade`. |
| 8.1 effectful | `wardens/spawn/route.py`, `wardens/spawn/spawn.py`, `wardens/ticks/alarms.py`, `wardens/deps.py` (`map`, `routing_rules`), `queen/deps.py` (`routing_rules`), `queen/forage/hosting.py` (per-slot floors) | Routing runs before the Guard's `slot_binding` check, on every spawn with no explicit target. The sub-bee is bound along the routed order; `llm.fallback` is recorded when the slot's own key was passed over; a shortfall files one `SHARED_SEATS` ForageRequest. A rebind with no target routes with the floor raised by `assignment.attempt`. Not carried: a handoff successor and a Queen-ordered REBIND bind a plain key's chain. |
| 8.2 | `llm/providers/ollama/` (`manage`, `client`, `models`, `protocol`, `fake`), `llm/providers/openai_compat/provider.py` (`list_models`), `cli/llm/local.py`, `cli/llm/rows.py` | `OllamaManager.detect(base_url)` probes the native API (`/api/tags` under the `/v1`-stripped base); never a name check. `hive llm models\|pull\|keep-alive --provider NAME`; `hive llm providers` appends `(ollama <version>)` when detected. The native models use `extra="ignore"` because Ollama adds fields between releases. |
| 8.3 | `manifest/schema/llm/servers.py` (`[llm.servers]`), `wardens/local_pool/hosting/` (`config`, `sources`, `report`, `launch`, `door`, `host`), `wardens/warden.py`, `wardens/autopilot/table.py` (`EVICT_SOURCE`), `wardens/ticks/{dispatch,control,heartbeat}.py`, `queen/ticks/sources.py`, `queen/forage/evict.py`, `llm/registry/provider_registry.py` (`add_provider`, `remove_provider`), `cell/local/{config,probe}.py` (`can_host_model`), `pheromone/events/families/resources.py` | `ModelServerHost` starts each `load_on_start` server through `CellSession.start` after the lease is held, waits for the adapter's `/models` health within `startup_timeout_s`, registers the provider, adds each source the ceilings allow to the map with its own Cell as host and sends `SourceAdded`; stop, a dead process (the heartbeat sweep), a released lease and an honoured `SourceEvict` reverse it. The Hive Stand reports `can_host_model` exactly when the manifest declares a server. `health_path` and `keep_alive_s` are validated but unused (the adapter probes `/models`). |
| 8.4 | `llm/prompts/overlay.py`, `llm/prompts/overlays/small_local/`, `llm/registry/overlay.py` (`OverlayingProvider`), `llm/registry/config.py` (`prompt_overlay`), `manifest/schema/llm/providers.py` (`prompt_overlay`), `hive/backends/provider_table.py`, `cli/in_cell/config.py` | An overlay directory holds `_system.md` (appended to every system prompt) and `<prompt_name>.md` (appended after the matching shipped body, before the labelled state sections). Applied at the provider door only; a Virtual Cell gets the same overlay through its provider rows. An unknown name fails when the provider is built. |
| 8.5 | `cli/llm/eval/` (`goals`, `evidence`, `grades`, `graders`, `runner`, `pipeline`, `measured`, `transcriber`, `report`, `commands`), `queen/forage/ledger/measured.py` + migration 0003, `cli/compose/hive/build.py` | `hive llm eval --manifest M [--goals a,b] [--update-map] [--report-dir D] [--fixtures DIR]`. One fresh Hive and database per goal; an attendant plays the human. Score to grade: `min(5, 1 + floor(score * 5))`; a source shared by several scored slots takes the lowest. `--update-map` writes the ledger, which `build_hive` reads into the map on the next start. WER grader over `<clip>.wav` + `<clip>.txt` pairs. |
| 8.8 | `llm/fanner/pricing.py`, `llm/fanner/lane.py`, `llm/models.py` (`Usage.estimated`), `llm/providers/openai_compat/estimate.py`, `pheromone/events/base.py` (`LlmUsage.priced`, `.estimated`) | Nothing priced a call before phase 8. The lane prices every metered call: tokens times the binding's per-million prices plus latency times the source's `cost_per_seat_hour_usd`. A provider-reported cost is kept. `priced=False` on the trail means unpriced, never free. A reply without a usage block is estimated from characters and flagged. `fanner/embed.py` and `fanner/transcription.py` still write `usage` without the two flags. |
| Hygiene | `scripts/check_no_model_ids.py` | A model-id prefix now matches only at a word start, so `ollama` passes and `llama3` still fails. |

## 3. How the pieces meet at runtime

- **A spawn.** `wardens/ticks/assign.py` builds a `WardenCellContext` with the Warden's last
  hosting plan; `spawn._plan_spawn` routes (`route_spawn`) unless a string override was given,
  then the Guard authorises the routed key; `_start_sub_bee` binds along `decision.order` through
  `deps.rebind` and re-links the chain, puts the routed effort on the head, records `llm.fallback`
  and files the shortfall request. The Fanner's own spill-over (3.12a) still runs at call time
  over the routed chain.
- **A hosted server.** `Warden.start()` leases, then `ModelServerHost.start_all()`. The Queen's
  `queen/ticks/sources.py` applies `SourceAdded` to her map and `ledger.seats`, and refuses a
  report about a Cell that is not the sender's own. `queen/forage/evict.py` sends `SourceEvict`
  only when the ledger shows that Warden exported seats and the map hosts the source on its Cell.
- **A priced call.** `FannerLane._record_success` prices the response and returns it priced, so
  the ladders and the trail see the same cost.
- **An overlay.** `ProviderRegistry.provider(name)` wraps the built provider when its config names
  an overlay; nothing above the registry knows.
- **Measured grades.** `build_hive` builds the ledger before the map and passes
  `ledger.measured.grades()` to `build_forage_map`. `hive llm slots`, `build_registry`,
  `cli/honey/context.py` and the in-Cell composition still build a map from hand-set grades.

## 4. Testing state and how to run the gates

The gates are those of the phase 7 handoff, section 4, unchanged. All were green at handover.
New suites:

- **Unit**: `tests/unit/llm/routing/` (two hypothesis properties: the chosen key is in the chain
  and the order is a permutation; a raised floor never lowers the chosen grade),
  `tests/unit/wardens/spawn/test_route.py`, `tests/unit/wardens/local_pool/hosting/`,
  `tests/unit/queen/ticks/test_sources.py`, `tests/unit/queen/forage/test_evict.py`,
  `tests/unit/llm/providers/ollama/`, `tests/unit/llm/fanner/test_pricing.py`,
  `tests/unit/cli/llm/eval/`, `tests/unit/llm/prompts/test_overlay.py`,
  `tests/unit/llm/registry/test_overlay.py`, `tests/unit/manifest/schema/llm/`.
- **e2e** (`tests/e2e/README.md` describes each): `test_routing_on_hive_stand.py`,
  `test_model_server_on_hive_stand.py`, and the offline suite (section 6).
- **Evals**: `tests/evals/routing/test_eval_harness.py` (fake provider, writes a report,
  `--update-map` changes `effective_grade`), `test_eval_harness_local.py` (`local_llm`).

## 5. Running it for real

The operator's path, in order:

1. Install Ollama (or any OpenAI-compatible server) and pull two chat models of different sizes
   and an embedding model; `docs/runbooks/moving-a-slot-to-local.md` walks the manifest edits.
2. `hive llm providers` and `hive llm models --provider local` to see the server; `hive llm test
   worker` for one call.
3. `HIVEMIND_LIVE_LLM=1 HIVEMIND_LOCAL_LLM_BASE_URL=... uv run pytest -m local_llm packages/
   hivemind/tests` for the gated suites.
4. `hive llm eval --manifest docs/manifests/offline.toml --report-dir docs/evals --update-map`
   with the base URL and model ids edited for the server: this writes exit criterion 2's table.
5. Repeat step 4 with `docs/manifests/full.toml` (a hosted key set) to get the Claude column.
6. The prompt portability pass (8.4): run `tests/unit/llm/prompts/` snapshots and the phase 3
   e2e scenarios with the worker slot on each local model; where a model fails, add an overlay
   directory and set `prompt_overlay` on its provider.

## 6. The offline proof (what the scripted runs proved, 2026-10-11)

`tests/e2e/offline/` (the rig: `guard` patches `socket.socket.connect`, `connect_ex` and
`socket.create_connection` to refuse any non-loopback address and to count loopback connects;
`manifest` derives each case's TOML from `docs/manifests/offline.toml` through `load_manifest`, so
the shipped manifest's offline validators run; `reads` asserts every `llm.call` names a provider
`hivemind.llm.registry.runs_locally` accepts). The whole e2e suite: 149 passed, 2 skipped (the two
X11 desktop-login cases; Xvfb is not installed here), 160 s.

- `test_offline_haiku`: in process and through `hive run`.
- `test_offline_virtual_cell`: the Drone's `worker.spawned` and `llm.call` come from the Cell's
  own node.
- `test_offline_ripening`: the SUMMARY row names the scripted ripener model, every row is embedded
  (the scripted server's `/v1/embeddings`), a paraphrase finds it with vectors in the ranking.
- `test_offline_spoken_goal`: a planted-bytes clip through `FannerTranscriptionGate`; one
  `llm.call` with audio seconds; no audio on any trail payload.
- `test_offline_clustering`: a real 503 outage mid-goal; `memory.checkpoint` lands before
  `task.paused`; the wake resumes from the Handoff; every file written once.
- `test_offline_src_unchanged`: the `src/` checksum and `git status` are identical across the
  offline and the fake-provider runs.
- `test_offline_vision` (8.7): a real Chromium login; `vision = false` offers and uses
  `browser_snapshot` only and sends no image part; `vision = true` offers, calls and sends one.

Three defects the scripted runs exposed are fixed in their own commits (`git log --grep "fix("`):
the Clustering pause raced the Handoff (`queen/cluster/protocol.py`), the health probe raised on an
in-process embedder's provider (`queen/cluster/tick.py`), and the OpenAI-compatible transcriber had
no `aclose`. A fourth is worked around, not fixed: the in-Cell Warden (`cli/in_cell/main.py`) never
closes its provider registry (`tests/e2e/offline/cells.py` closes it for the harness); a real fix
needs the registry returned from `build_in_cell_warden_deps`, whose parameter list is at the limit.
The `tests/evals/offline/` cases (real-model haiku, in-process Whisper, a real-model Forager) are
`local_llm`-gated; the `local_llm` job runs them with Ollama at two model sizes.

## 7. Open items, in suggested order

1. **Exit criterion 2** needs the operator's real-model run (section 5).
2. **The 8.4 pass** needs the same machine.
3. **Measured grades reach only `build_hive`.** `hive llm slots`, `build_registry`,
   `cli/honey/context.py` and the in-Cell composition build their map from hand-set grades.
4. **A handoff successor and a Queen-ordered REBIND bind a plain key**, without the routed order
   or effort.
5. **`fanner/embed.py` and `fanner/transcription.py`** write `usage` without `priced`/`estimated`;
   `observation/views/trail.py` does not expose the two flags.
6. **`ServerSpec.health_path` and `keep_alive_s`** are carried and validated but unused.
7. **The Hive Stand trail holds two `forage.source_added` rows per source** (Warden and Queen),
   told apart by `reporter`.
8. **A Virtual Cell gets no `[llm.servers]`** (`cli/in_cell/deps.py` passes `servers={}`); a
   Nuc (phase 11) is where a device-side server is declared.
9. **Weights from the Basket** wait for 9.2a.
10. **Step 6.13** waits for the operator's writing instructions.
11. **The in-Cell Warden never closes its provider registry** (section 6).
12. **The Clustering e2e reads a private field** (`bee.runtime._ctx.telemetry.handoff_requested`)
    to know a HANDOFF landed; a public signal would let it stop.

## 8. Traps

- Everything in the phase 7 handoff's section 9 still holds (`cd` inside compound commands,
  `build_hive` outside a running loop, click's stderr mixing, load-sensitive e2e).
- `scripts/check_no_model_ids.py` scans test files too: a model id in a test string fails CI.
- The class-size limit counts a class's whole span, docstring included: `WardenDeps`,
  `QueenDeps`, `ForageLedger` and `FannerLane` all sit within a line or two of 200.
- `llm/__init__.py` is at its 300-line limit; new names are exported from the sub-package faces
  (`hivemind.llm.routing`, `hivemind.llm.prompts`, `hivemind.llm.registry`).
- `manifest/schema/` and `queen/ticks/` and `cli/llm/eval/` are at the ten-module limit.
- The Queen's hosting plan ranks planned sources first in routing; a source the plan excludes
  (below the slot's floor) is never routed to, which is why `test_routing_on_hive_stand`'s LOW
  case uses a grade-2 primary.

## 9. Next steps

Phase 9 (Royal Jelly Lab and Comb Registry) is next in the roadmap; it depends on phases 5 and 7,
both done. Phase 11 builds Nucs on 8.3's `ModelServerHost` and the hosting plan.
