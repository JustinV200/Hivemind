# Phase 7 handoff (Honey Store): state, map, how to run it, open items

> For an agent starting with a clean context. Roadmap phase 7 (steps 7.1 to 7.11 and 7.9a) was
> built on 2026-09-24 on branch `claude/vigilant-hawking-qf81zr` in a Linux cloud container. One
> orchestrator handed the work to seven large subagent tasks (D1 to D7), then ran the Hive for real
> and fixed what those runs found. **The branch is pushed but not merged, and it has no PR.**
> Phase 6 (Exoskeleton) was not built first because the operator asked for phase 7 directly.
> Phase 6's Forager therefore does not exist, and a Drone stands in for it in the first exit
> criterion.

Read these first: `CLAUDE.md`, `.claude/codingrules.md`, `.claude/roadmap.md` phase 7 (its "Met"
note lists every deviation from the step text), `docs/adr/0035-honey-store-sqlite-fts5-sqlite-vec.md`,
`docs/adr/0036-embedding-provider-and-reembedding-policy.md`, then
`packages/hivemind/src/hivemind/honey_store/README.md` and the READMEs of its sub-packages.

## 1. Where things stand

- Branch `claude/vigilant-hawking-qf81zr` holds 25 phase 7 commits plus this handoff, on top of
  `origin/main` at `67b3232`. Everything is pushed and the working tree is clean.
  `git log --oneline origin/main..HEAD` lists the commits. Each one lands a step or a fix, and its
  body says why. Do not open a PR unless the operator asks for one.
- Every box from 7.1 to 7.11, plus 7.9a, is ticked. The exit criteria have a dated "Met" note that
  names two stand-ins: a Drone for the Forager, and a stand-in chat model in the `local_llm` run.
- All gates were green at handover across the whole repo (section 4 has the commands): 6964
  passed, 3 skipped and 9 deselected (the `integration`, `live_llm` and `local_llm` markers). That
  run includes e2e and took about 65 s of test time in this container.
- The Waggle protocol is now **1.7**. It adds `TaskAssign.honey` and the ids `NectarId`
  (`nectar_…`) and `HoneyId` (`honey_…`). `HoneyQuery`, `HoneyResponse` and `NectarDeposit`
  already existed in the catalogue. `docs/waggle/spec.md` records the version bump.
- Dependencies:
  - `sqlite-vec` 0.1.x is a new runtime dependency, loaded into SQLite as an extension. On a host
    whose `sqlite3` cannot load extensions, a pure-Python fallback ranks identically.
  - `embeddings` is a new optional extra (`sentence-transformers`), re-exported at the workspace
    root.
  - `.github/workflows/integration.yml` now syncs `--extra docker` rather than `--all-extras`, so
    CI never installs a deep learning framework.
- There is one new migration, `honey_store/schema/migrations/0001_create_honey_store.sql`. It
  creates `honey_nectar`, `honey`, `honey_vectors(honey_seq, model, dims, blob)`,
  `honey_watermarks`, `honey_proposals`, the FTS5 external-content table `honey_fts` (porter
  unicode61) and its three sync triggers. The Honey Store lives in the Hive's single SQLite file,
  on its own connection (`cli/stores.open_honey_store`).
- The sandbox limited the testing: its proxy reaches PyPI but not a model hub. No real chat model
  and no Hugging Face weights were available (section 5 explains how the real runs worked around
  this).

## 2. What phase 7 implemented (map)

Paths are relative to `packages/hivemind/src/hivemind/` unless they say otherwise.

| Step | Modules | Notes |
|---|---|---|
| 7.1 | `llm/embedding/` (provider, models, bound, capabilities, gate, fake), `llm/providers/openai_compat/embedding.py`, `llm/providers/sentence_transformers/embedding.py`, `llm/fanner/embed.py`, `llm/registry.py` | `EmbeddingProvider` protocol and `FakeEmbedding`. `registry.embedder(ModelSlot.EMBEDDER)` returns a `BoundEmbedder`, which refuses any response whose `model` differs from the bound one. Anthropic has no embedding endpoint (`embedding_unsupported`). `FannerLane.embed` meters each call as `llm.call` with slot `EMBEDDER`, and on an outage it walks only same-model fallbacks. sentence-transformers is imported lazily and only by its adapter. Contract suite: `tests/contracts/test_embedding_provider_contract.py`, over all three adapters (no network, no real model library). |
| 7.2 | `honey_store/schema/`, `honey_store/store/` (`protocol.py`, `fts.py`, `sqlite/` store, nectar, honey, search, vec, vectors, filters, stats) | Vectors sit in a plain table and are ranked with `vec_distance_cosine`. There is no `vec0` table: ADR-0035 measured it as no faster, because sqlite-vec 0.1 has no approximate index. Vectors are kept per model, and several models' vectors coexist (ADR-0036). Contract suite: `tests/contracts/test_honey_store_contract.py`, over a temp file with sqlite-vec, a temp file with the Python fallback, and `:memory:`. |
| 7.3 | `honey_store/models/` (nectar, honey, search), `clearance.py`, `scope.py`, `identity.py`, `errors.py` | Provenance and clearance are mandatory. Intake gives C2 to HUMAN and WATCH origins and to anything from a borrowed (Real) Cell. Labels only rise automatically; lowering one needs a JUDGE or HUMAN approver. Reader ceiling = min(requested, principal, tier matrix), and Night Veil is capped at C1. Scopes are `hive`, `cell:<id>`, `bee:<id>` and `task:<id>`. |
| 7.4 | `honey_store/nectar/` (intake, submission, reassembly), `workers/nectar.py` | `NectarIntake.submit`, `receive_chunk` and `expire_groups`. The size cap is `[honey.store] max_nectar_bytes`. Dedupe goes by `source_key` first, then by sha256, partitioned across the Night Veil boundary. Events are built inside the same transaction as the rows (`NectarEvents`). A Worker's deposit is split into `NectarDeposit` chunks and reassembled by `ChunkGroups`. |
| 7.5 | `honey_store/ripening/` (pipeline, chunk, summarise, embed, dedupe, index, drafts, deps), `llm/prompts/ripen_nectar.md` | `Ripener.run_pass()` returns a `PassOutcome` and runs two stages. Stage one, `ripen_pending`: chunk, summarise on RIPENER through `complete_structured` (`RipenedSummary`, whose label may only rise), dedupe, then index SUMMARY and CHUNK rows. Stage two, `embed_pending`: embed fresh rows, and re-embed rows that lack a vector for the current model, a bounded number per pass. The summary is heuristic when there is no RIPENER, or when the deposit is under `summarise_min_chars` (default 400). |
| 7.6 | `workers/roles/house_bee/` (loop, honey, sweep), `queen/ticks/housekeeping.py`, `cli/compose/hive.py` | `HouseBeeRipening` runs in the Queen's process every `[honey.ripening] interval_s` (default 30). Each pass drains queued operator notes, then runs one Ripener pass. The Queen's housekeeping sweep deposits aged Bee Bread and retired Cell Wax (`deposit_aged_bee_bread`, `deposit_retired_wax`). Nothing gathered on a Night Veil Cell is deposited. |
| 7.7 | `honey_store/honey/` (retrieve, rank, budget), `memory/hot_state/` (retrieved, packing, summaries) | `HoneyRetriever.search` and `search_outcome` rank hybrid (section 3 has the formula). The token budget scales with the bound model's window, and the result becomes the cold tier in `memory.assemble`. Tainted and retired rows are never returned (`store/sqlite/filters.py`). |
| 7.8 | waggle `messages/task/assignment.py`, `ids.py`, `envelope.py`; `queen/ticks/honey.py`, `wardens/ticks/honey.py` and `dispatch.py`, `workers/runtime/honey.py`, `workers/tools/honey.py`, `workers/context.py`, `supervision/attendant/items.py` | The Warden relays `HoneyQuery` and `HoneyResponse`, matching them by `InboxItem.correlation_id`. Workers get the `recall` and `remember` tools; a `recall` times out after 30 s. Hits reach a model only as the delimited `<<<retrieved>>>` untrusted block. |
| 7.9 | `queen/dispatcher/honey.py` (`consult_for_plan`, `consult_for_assignment`), `queen/dispatcher/ready.py`, `queen/planner/plan.py` (`PlanBrief.honey`), `queen/goal_submission.py`, `queen/ticks/results.py` | The planner sees Honey for the goal. Each assignment searches the task's targets, then the chosen Cell's own scope, and attaches the hits to `TaskAssign.honey`. Each consultation records one `queen.honey_consulted`. Every verified outcome is deposited as TASK_OUTCOME Nectar with `source_key` `task_outcome:<task id>`. |
| 7.9a | `workers/roles/house_bee/honey.py`, `queen/ticks/housekeeping.py` (`QueenCellRecords`), `honey_store/browse/sources.py` (`LiveWaxSource`) | Cleared and expired wax ripens at `cell:<id>` scope, keeping its severity. The pre-check attaches that history next to the Cell's live wax, and `/cells/<id>/wax` lists the live wax. |
| 7.10 | `honey_store/scope.py`, `guard/capabilities.py` (`honey:read:<scope glob>`), `honey_store/browse/` (browser, folders, listing, documents, paths, notes, relabel, sources, fake, errors) | `HoneyBrowser` is read-only over `/hive`, `/cells/<id>` (with `wax`), `/bees`, `/tasks` and `/bee-bread`. `HoneyRelabeller` raises or lowers a label, given an approver. `propose_note` files a PROPOSED Cell Wax from a Cell's folder; from any other folder it queues a note, and the House Bee drains the note into HUMAN Nectar. |
| 7.11 | `cli/honey/` (query, browse, maintain, context, render, sources), `cli/compose/honey.py`, `cli/stores.py`, `common/logging.py`, `cli/app.py` | `hive honey --manifest M [--clearance C] stats\|query\|ls\|cat\|propose\|relabel\|ripen --now\|reembed`. Operator commands record the actor `human`. CLI logs go to stderr at WARNING level. |
| Config | `manifest/schema/honey.py`, `manifest/schema/llm.py`, `docs/manifests/{minimal,local,full}.toml` | New sections `[honey.store]`, `[honey.ripening]` and `[honey.retrieval]`, and new slots `embedder` and `ripener`. `minimal.toml` (Anthropic only) ripens with model-written summaries but searches full text only, because Anthropic serves no embeddings. That is by design. |
| Trail | `pheromone/events/families.py` | New events: `honey.nectar_received`, `honey.nectar_deduplicated`, `honey.nectar_rejected`, `honey.ripened`, `honey.ripen_failed`, `honey.reembedded`, `honey.queried`, `honey.label_raised`, `honey.label_lowered`, `honey.retired`, `honey.note_proposed`, and `queen.honey_consulted`. |
| Drone | `workers/roles/drone/prompt.py` | The reply reserve is `min(4096, 0.25 * window)`, so a small model still has room for Honey (section 7). |

## 3. How the pieces meet at runtime

- **Composition.** `cli/compose/honey.build_honey_access` is the only place a `HoneyAccess` is
  built. `HoneyAccess` bundles the store, intake, retriever, Ripener, identity and the three config
  sections. `build_hive` and every `hive honey` command call this builder. It resolves EMBEDDER
  and RIPENER once and degrades instead of failing (ADR-0036): an unusable slot becomes None and is
  logged once, as `honey.embedder_unavailable` or `honey.ripener_unavailable`. Summary and embed
  calls run on a LOW-accuracy Fanner lane, while a query's own embedding runs on an ordinary lane.
  The result reaches the Queen as `QueenDeps.honey` and the operator as `Hive.honey`.
- **Deposits.** Four sources feed intake:
  - the Queen's verified task outcomes;
  - Workers, through the `remember` tool;
  - the housekeeping sweep's aged Bee Bread and retired wax;
  - operator notes.

  All of them go through `NectarIntake`, which labels, scopes, dedupes and records events in one
  transaction.
- **Ripening.** The running Hive ripens only through the House Bee loop. `hive honey ripen --now`
  runs one Ripener pass, but it does not drain notes (see open item 7).
- **Reading.** Every read goes through `HoneyRetriever` with a `HoneyReader`, which carries the
  capabilities, the clearance ceiling and the Night Veil flag.
- **Ranking.** Each text match scores `max(x/(1+x), 0.5 * x/x_best)`, where `x = max(0, -bm25)`
  and the second term is the relative floor for young stores. Each vector hit scores
  `clamp(1 - cosine distance)`. The two are fused with weights 0.4 (text) and 0.6 (vector) and cut
  at `min_score` 0.15. At most two hits come from any one Nectar, and the rest are packed to the
  token budget. If the vector side has no candidates (no embedder, or no vectors yet for the
  current model), it counts as unavailable and ranking is text-only at weight 1.0.
- **Night Veil.** One deposit from a Night Veil Cell is recorded like ordinary Nectar: the
  RIPENED_HONEY export at C0 or C1. Every other one is EPHEMERAL (no events, purged at teardown),
  and a Night Veil query records no event (ADR-0035).

## 4. Testing state and how to run the gates

Run everything from the repo root with `uv run --frozen`. The gates CI runs, in order:

```sh
uv run --frozen ruff format --check .
uv run --frozen ruff check .
uv run --frozen mypy
uv run --frozen lint-imports
for s in check_sizes check_fanout check_no_model_ids check_no_kind_branches check_no_transcripts; do
  uv run --frozen python scripts/$s.py || break
done
uv run --frozen pytest -q -p no:cacheprovider \
  -m "not integration and not live_llm and not local_llm" packages scripts/tests
```

- **Unit tests** mirror every new module: `tests/unit/honey_store/…`, `unit/llm/…`,
  `unit/cli/honey/…` (which drives the real typer app over a real SQLite file), `unit/workers/…`,
  `unit/queen/…` and `unit/wardens/…`. Shared builders are `tests/builders/honey.py`,
  `honey_wire.py` and `house_bee.py`.
- **e2e: `tests/e2e/test_honey_compounds.py`** covers exit criteria 1 and 2:
  - (a) One goal runs twice on the Hive Stand at C2, parametrized over model capabilities `full`
    and `none`. The second `TaskAssign` carries the first run's outcome, `queen.honey_consulted` is
    on the trail, and the second run takes fewer Drone calls.
  - (b) At C1, nothing is attached.
  - (c) A phase 4 Handoff is found a day later with full provenance. The test uses a FakeClock and
    drives the Queen's housekeeping tick directly.
- **e2e: `tests/e2e/test_honey_wire.py`** has a Worker `recall` Honey over real Waggle and deposit
  a two-chunk finding that reaches intake.
- **eval: `tests/evals/honey/test_ripening_local.py`** is `local_llm` and covers exit criterion 3.
  It loads `docs/manifests/local.toml` and builds the store through the production builder. One
  pass must summarise on RIPENER (the row records its `ripener_model`) and embed every row. A
  paraphrased query must then hit with vectors in use. It skips unless `HIVEMIND_LIVE_LLM=1` and
  `HIVEMIND_LOCAL_LLM_BASE_URL` are set; `HIVEMIND_LOCAL_RIPENER_MODEL` and
  `HIVEMIND_LOCAL_EMBED_MODEL` replace the slot models. Run it with
  `pytest -m local_llm packages/hivemind/tests/evals/honey`. `tests/evals/README.md` has a section
  on it.
- Commit only behind green gates. Twice this phase a commit went in with `check_sizes` failing (a
  function over 50 lines) and needed a follow-up split.

## 5. Running it for real

The scripts below lived in the session's scratchpad, which is gone. Rebuild them from these
descriptions if you need to rerun anything.

- **Local model shim.** WordLlama's weights ship inside its PyPI wheel, so it works without a
  model hub. Make a separate venv and run `pip install wordllama`, then load the model with
  `WordLlama.load(cache_dir=…, disable_download=True)`. A ~150-line `ThreadingHTTPServer` on
  `127.0.0.1:8765` implements the OpenAI-compatible routes:
  - `GET /v1/models` lists `wordllama-l2-supercat`, `wordllama-l2-supercat-256` and
    `local-extractive-ripener`. The `-256` model is the same model truncated to 256 dimensions
    (Matryoshka) and renormalised.
  - `POST /v1/embeddings` returns the OpenAI shape and echoes the requested model id.
  - `POST /v1/chat/completions` handles ripening. It finds the `Text (the first N of M
    characters):` marker from `ripen_nectar.md` and answers `{title, summary, key_facts,
    clearance: "C1", clearance_reason}`, built from the deposit's own sentences. It answers `{}` to
    anything else.
  - `GET /stats` returns call counts.

  To stop the server, find its PID with `ps -eo pid,args | grep "[e]mbed_server"` and kill that
  PID. **Never use `pkill -f`**: the pattern matches your own shell's command line and kills it.
- **Manifest for real runs.** Start from `tests/builders/cli.fake_manifest(root)`. Rebind
  `[llm.slots.embedder]` and `[llm.slots.ripener]` to a provider `local` with
  `kind = "openai_compat"` and `base_url = "http://127.0.0.1:8765/v1"`. Give that provider the
  capabilities `native_tool_calls = false`, `schema_output = false`, `json_mode = true` and
  `context_window = 8192`. Set `[honey.ripening] interval_s = 2.0` and
  `summarise_min_chars = 100`, so the short outcome reaches the RIPENER. Point
  `HIVEMIND_LOCAL_LLM_BASE_URL` at the same URL to run the `local_llm` eval.
- **Two-run driver** (`p7_real.py`, about 220 lines).
  1. Build the Hive **outside** any event loop: `build_hive(load_manifest(path, {}), environ={},
     clock=SystemClock(), responders={"fake": brain.responder})`.
  2. Inside `run_hive(hive)`, call `run_goal(hive, GOAL, clearance=HoneyClearance.C2,
     timeout_s=120)`.
  3. Wait until `hive.honey.store.has_source("task_outcome:<task id>")` is true and `stats()`
     shows Honey. Let the House Bee's own loop do the ripening; do not call a pass by hand.
  4. Run the same goal again.

  The scripted "brain" plans one task (`FILE_EXISTS version.txt`) and always approves in the judge.
  Its Drone reads its own system prompt. If the `<<<retrieved>>>` block holds the Python version,
  the Drone writes the file straight away. Otherwise it discovers the version in three calls
  (run → read → answer), and has the command write the file itself, because `run_command` returns
  no stdout in v0. The driver counted WORKER-slot calls inside the brain, captured every
  `TaskAssign` by wrapping `Codec.encode`, and read the `llm.call` events by slot from the trail.
- **CLI walk.** Run against the real run's `hive.sqlite3`:
  - `hive honey --manifest M stats`
  - `hive honey --manifest M query "python version"` (quote multi-word queries)
  - `hive honey --manifest M ls /tasks` and `cat <path>`
  - `hive honey --manifest M propose /hive "title" "note text"`
  - `hive honey --manifest M relabel <path> C1 --reason "why"` (`--reason` is required)
  - `hive honey --manifest M ripen --now`
  - `hive honey --manifest M reembed`

  To test an embedder switch, change the embedder model to `wordllama-l2-supercat-256`, run
  `reembed`, and confirm `stats` shows both models' vectors.

## 6. What the real runs proved (2026-09-24)

- **Compounding on the Hive Stand.** The runs used a real lease, SQLite, Queen, Warden and Drones,
  a real `python3` subprocess, real WordLlama vectors through the OpenAI-compatible adapter, and the
  House Bee ripening on its own 2 s interval.
  - Run 1 needed 3 Drone calls, and run 2 needed 2.
  - Run 2's `TaskAssign.honey` carried run 1's outcome, and the planner's brief showed it.
  - `queen.honey_consulted` and `honey.ripened` were on the trail.
  - EMBEDDER and RIPENER calls were metered as `llm.call` with their slots.
- **Degradation.** While the shim was stopped, ripening fell back to heuristic summaries, rows
  were left without vectors, and queries fell back to full text. Each fallback was logged, and
  nothing raised. After the shim came back, the House Bee's passes and `hive honey reembed` filled in the
  missing vectors.
- **Operator notes.** A running Hive's House Bee drained a note from `hive honey propose` into
  HUMAN Nectar and ripened it.
- **Embedder switch.** The new model's vectors sit beside the old ones, and ranking stays
  text-only until the new vectors exist. This run found the last fix in section 7.
- **CLI.** Every `hive honey` command worked against a real run's database, and the events name
  the actor `human`.

## 7. Defects found and fixed this phase

The commit bodies have the details.

- `dc5bb64`: on a young store, bm25 weighted every word at about 1e-6, so full-text search found
  nothing. The fix floors each match relative to the query's best, and drops function words when
  the query has other words. `min_score` rose from 0.05 to 0.15, because a real embedder fuses
  unrelated text at about 0.11.
- `70c022e`: the Drone's fixed 4096-token reply reserve left an 8k model 204 tokens for Honey,
  too few for a single hit. The reserve is now capped at a quarter of the window.
- `f463838`: CLI logs went to stdout and corrupted `--json`. Logs now go to stderr through the
  `_CurrentStderr` proxy.
- `feefc23`: `withheld` counted rows the query's own scope narrowing left out. It now counts only
  rows that policy (scope capabilities and clearance) withheld. `hive honey query` also recorded
  its event as `system`; it now records `human`.
- `93e58f4`: after an embedder switch the vector side was empty, yet fusion still weighted it,
  scaling every text score down to 40% and under `min_score`. An empty vector side now counts as
  unavailable.
- `8b189c5`: the Python vector fallback took 513 ms at 5k rows of 768 dimensions. With
  `math.sumprod` it takes 208 ms; sqlite-vec takes about 20 ms. The fallback is still linear.
- Reviews of the subagents' work found further defects. The fixes were folded into the step
  commits; see the "Review fixes on top" paragraphs of `d3be476`, `d0b84d2` and `3052cce`.
  - **Night Veil boundary.** An ordinary deposit could dedupe onto a Night Veil Cell's ephemeral
    row and vanish with that row's purge. An ephemeral deposit could raise a persistent row's
    label. `ripen()` could turn an ephemeral row into Honey, which hid it from the purge.
  - **Labels.** A Real Cell's Bee Bread and Cell Wax got no C2 floor. `ripen()` now raises every
    part to its Nectar's current label, in the same transaction.
  - **Embeddings.** The Fanner's embed path never tried a same-model fallback. The fake embedder
    reported its own model id rather than the binding's, which would have made every stored vector
    look stale to a re-embed. The OpenAI-compatible adapter accepted fewer vectors than texts. A
    declared `sentence_transformers` provider broke every Virtual Cell's provider table, and
    `hive llm slots` and `hive llm providers` crashed on an embedding-only provider.
  - **Store.** One stored vector of a different length failed the whole vector query. The FTS
    update trigger re-indexed on every relabel and re-embed. A live-wax path parsed as a Cell
    scope.

## 8. Open items, in suggested order

1. **Re-run exit criterion 1 with the real Forager** once phase 6 lands, and re-tick with a note.
2. **Run the `local_llm` eval against a real local chat model** (LM Studio or Ollama, loaded with a
   JSON-capable model). So far it has only met the extractive stand-in. The
   `sentence_transformers` adapter has never loaded a real model either (the hub was blocked).
3. **Hive Stand Honey is C2, but `hive run` defaults to `--clearance C1`.** A default run on the
   Hive Stand therefore cannot read what earlier runs learned there. This is the rule working as
   written (ADR-0035), but operators will be surprised. Either document `--clearance C2` or let
   the operator declare an intake floor for the Hive Stand. The second option is a policy change
   and needs an ADR amendment.
4. **The Hive Stand's Cell id changes on every `hive run`** (a phase 5 carry-over). As a result,
   `cell:<id>` Honey, 7.9a's wax history and `/cells/<id>` never compound across processes on the
   Hive Stand. It needs a stable id, derived from the manifest's node id or persisted.
5. **Lowering a label through a judge-reviewed Capping proposal is not wired.** `HoneyRelabeller`
   accepts a JUDGE approver, but only the CLI calls it (with HUMAN). This belongs with the phase 10
   Capping and judge work.
6. **"Propose a note" is a queued row, not a Queen inbox message** as step 7.10's text says. Either
   route it to her inbox or amend the roadmap.
7. **`ripen --now` does not drain notes.** Drained notes are attributed to the Hive Stand's Cell
   id, which the CLI lacks without a running Hive (tied to item 4).
8. **Sends over a closed link crash a tick** (pre-existing, found by D5). Queen and Warden sends
   such as `questions.forward_answer` and wax `_send_written` raise out of the tick, and
   `TickLoop` treats that as fatal. Guard it at the send helper.
9. **Identical outcomes dedupe away repeat provenance.** Two tasks whose outcome text is
   byte-identical dedupe by sha256 onto the first row, and only a `honey.nectar_deduplicated`
   event names the second task. If "confirmed twice" should count, record the extra source.
10. **The `"hive_stand"` source literal is defined three times**: `queen/ticks/housekeeping.py`,
    `supervision/capping/leave/persist.py`, and a bare string in `queen/dispatcher/snapshot.py`.
    Give it one home in `hivemind.cell`, beside `cell/local/source.py`'s `_SOURCE_NAME`.
11. **Scale limits**:
    - Browser index folders (`/cells`, `/bees`, `/tasks`) scan at most 5,000 rows
      (`browse/folders.py` `MAX_SCAN_ROWS`).
    - Old-model vectors are never pruned.
    - The Python fallback is linear.
    - `min_score` and the fusion weights were calibrated on one embedder, and each embedder needs
      its own calibration (phase 8's eval).

## 9. Traps

- `build_hive` must be called **outside** a running event loop. It uses `asyncio.run` internally
  for store setup.
- The Drone's prompt text mentions the `<<<retrieved>>>` delimiter itself. To find the real block,
  search from its end marker (`rfind("<<<end retrieved>>>")`).
- click 8.5 mixes stderr into `result.output`, so assert on `result.stdout` in CLI tests. Loggers
  must write through `_CurrentStderr`, because `CliRunner` swaps `sys.stderr` on each invoke.
- `hive honey` options come **before** the subcommand: `hive honey --manifest M --clearance C1
  query "…"`.
- A trail event's `subject_id` must start with a known waggle `IdKind` prefix, and
  `pheromone/events/base.py` enforces this. That is why waggle gained `NectarId` and `HoneyId`. A
  new kind of subject needs a new `IdKind`, not a free-form string.
- A hit's clearance can be higher than its Nectar's declared label, because intake applies floors.
  A C2 hit from the Hive Stand is correct, so do not "fix" it.
- FTS5's bm25 is nearly flat on a young store. Keep the relative text floor in `rank.text_scores`,
  and test ranking changes on a small store as well as a large one.
- A `cd` inside a compound shell command can move the session's working directory. Start compound
  commands with `cd /home/user/Hivemind`.

## 10. Next steps

Phase 8 (local models and provider routing) is next in the roadmap. Before starting it, settle
open items 3 and 4 with the operator, because both change what a default `hive run` on the Hive
Stand can remember. Phase 8's evals are also where item 11's per-embedder calibration belongs.
Phase 6 remains unbuilt. If the operator wants the roadmap order restored, build phase 6 next and
then re-run item 1.
