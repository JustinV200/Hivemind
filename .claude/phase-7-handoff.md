# Phase 7 handoff (Honey Store): state, map, how to run it, open items

> For an agent starting with a clean context. Roadmap phase 7 (steps 7.1 to 7.11 and 7.9a) was
> built on 2026-09-24 on branch `claude/vigilant-hawking-qf81zr` in a Linux cloud container, in two
> sessions. The first built every step and handed over eleven open items. The second worked through
> them after the operator made two decisions:
>
> - Honey learned on the Hive Stand reaches a default `C1` goal through judge-reviewed label
>   lowering (ADR-0034).
> - A Drone keeps standing in for phase 6's Forager, which is still unbuilt.
>
> The second session then ran the Hive on real local models and fixed what those runs found. Phase
> 7 is complete apart from the Forager stand-in. **The branch is pushed but not merged, and it has
> no PR.**

Read these first:

- `CLAUDE.md` and `.claude/codingrules.md`.
- `.claude/roadmap.md` phase 7. Its "Met" note lists every deviation from the step text.
- ADRs 0031 to 0034 in `docs/adr/`.
- `packages/hivemind/src/hivemind/honey_store/README.md` and the READMEs of its sub-packages,
  `lowering/` above all.

## 1. Where things stand

- **The branch.** It holds 50 commits on top of `origin/main` at `67b3232`: the first session's 26
  (its handoff included) and the second session's 24. Everything is pushed and the working tree is
  clean. `git log --oneline origin/main..HEAD` lists the commits, and each commit body says why. Do
  not open a PR unless the operator asks for one.
- **The roadmap.** Every box from 7.1 to 7.11, plus 7.9a, is ticked. The exit criteria's dated
  "Met" note names one stand-in: a Drone for the Forager.
- **The gates.** All were green at handover across the whole repo (section 4 has the commands):
  7259 passed, 3 skipped and 16 deselected (the `integration`, `live_llm` and `local_llm` markers).
  The run includes e2e and takes about 70 s of test time in this container.
- **Waggle.** The protocol is **1.6**, unchanged by the second session. It adds `TaskAssign.honey`
  and the ids `NectarId` (`nectar_…`) and `HoneyId` (`honey_…`).
- **Migrations.** There are three, all in `honey_store/schema/migrations/`:
  - `0001_create_honey_store.sql` creates `honey_nectar`, `honey`, `honey_vectors`,
    `honey_watermarks`, `honey_proposals` (queued operator notes), the FTS5 table `honey_fts` and
    its triggers.
  - `0002_nectar_sources.sql` creates `honey_nectar_sources`, the repeat sources of ADR-0033.
  - `0003_label_lowering.sql` adds the three labelling facts to `honey_nectar` (declared, floor and
    the Ripener's reading, each with its rank, plus the reading's reason). It also creates
    `honey_lowerings` (ADR-0034).

  The Honey Store lives in the Hive's single SQLite file, on its own connection.
- **Dependencies.** Nothing new since the first session:
  - `sqlite-vec` 0.1.x, with a pure-Python fallback that ranks identically;
  - the optional `embeddings` extra (`sentence-transformers`);
  - CI syncs `--extra docker` only.
- **Real models.** In the second session the proxy reached Docker Hub, and its `ai/` namespace
  serves models as OCI artifacts, so the runs used real local models. `RIPENER` was
  qwen2.5-3B-Instruct, `JUDGE` qwen2.5-7B-Instruct and `EMBEDDER` nomic-embed-text v1.5, all GGUF
  behind llama-cpp-python's OpenAI-compatible server. Qwen3-Embedding 0.6B also ran in process
  through sentence-transformers. Section 5 has the method.

## 2. What phase 7 implemented (map)

Paths are relative to `packages/hivemind/src/hivemind/` unless they say otherwise.

| Step | Modules | Notes |
|---|---|---|
| 7.1 | `llm/embedding/` (provider, models, bound, capabilities, gate, fake, chain), `llm/providers/openai_compat/embedding.py`, `llm/providers/sentence_transformers/embedding.py`, `llm/fanner/embed.py`, `llm/registry.py` | `EmbeddingProvider` protocol and `FakeEmbedding`. `registry.embedder(ModelSlot.EMBEDDER)` returns a `BoundEmbedder`, which refuses any response whose `model` differs from the bound one. Anthropic has no embedding endpoint (`embedding_unsupported`). `FannerLane.embed` meters each call as `llm.call` with slot `EMBEDDER`, and on an outage it walks only same-model fallbacks (`chain.py`). `ProviderRegistry.aclose()` closes every built adapter's pooled connections. Contract suite: `tests/contracts/test_embedding_provider_contract.py`. |
| 7.2 | `honey_store/schema/`, `honey_store/store/` (`protocol.py`, `fts.py`, `sqlite/`: store, nectar, sources, honey, search, vec, vectors, filters, stats, lowering) | Vectors sit in a plain table and are ranked with `vec_distance_cosine`. There is no `vec0` table, because sqlite-vec 0.1 has no approximate index (ADR-0031). Vectors are kept per model, and several models' vectors coexist (ADR-0032). Contract suites: `tests/contracts/test_honey_store_contract.py` and `test_honey_store_lowering_contract.py`, over a temp file with sqlite-vec, a temp file with the Python fallback, and `:memory:`. |
| 7.3 | `honey_store/models/` (nectar, honey, search), `clearance.py`, `scope.py`, `identity.py`, `errors.py` | Provenance and clearance are mandatory. Intake gives C2 to HUMAN and WATCH origins and to anything from a borrowed (Real) Cell, and keeps the declared label and the floor as facts. Labels rise automatically. Only a JUDGE or HUMAN approver lowers one (see the lowering row). `clearance.held_by_floor_alone` says whether only the Real Cell floor holds a label up. Reader ceiling = min(requested, principal, tier matrix), and Night Veil is capped at C1. |
| 7.4 | `honey_store/nectar/` (intake, submission, reassembly), `workers/nectar.py`, `store/sqlite/nectar.py`, `store/sqlite/sources.py` | The size cap is `[honey.store] max_nectar_bytes`. Dedupe goes by `source_key` first, then by sha256, partitioned across the Night Veil boundary. A duplicate with different provenance records a repeat source (ADR-0033). A merge raises the label and keeps the higher of each fact. It never re-applies a Real Cell floor that an approved lowering already cleared for that text (ADR-0034). Events are built inside the same transaction as the rows. |
| 7.5 | `honey_store/ripening/` (pipeline, chunk, summarise, embed, dedupe, index, drafts, deps, prune), `llm/prompts/ripen_nectar.md` | `Ripener.run_pass()` runs two stages. Stage one chunks, summarises on RIPENER through `complete_structured`, dedupes and indexes. Stage two embeds, and re-embeds rows that lack a vector for the current model. The Ripener labels the text itself and its reading is stored (ADR-0034). The summary is heuristic without a RIPENER, and for a deposit under `summarise_min_chars` (default 400), unless only the floor holds its label up. `prune_vectors` drops other models' vectors on request (ADR-0033). |
| 7.6 | `workers/roles/house_bee/` (loop, honey, sweep), `queen/ticks/housekeeping.py`, `cli/compose/hive.py` | `HouseBeeRipening` runs in the Queen's process every `[honey.ripening] interval_s` (default 30). A pass drains queued operator notes, runs one Ripener pass, then files and reviews label lowerings. The Queen's housekeeping sweep deposits aged Bee Bread and retired Cell Wax. Nothing gathered on a Night Veil Cell is deposited. |
| 7.7 | `honey_store/honey/` (retrieve, rank, budget), `memory/hot_state/` (retrieved, packing, summaries) | `HoneyRetriever.search` and `search_outcome` rank hybrid (section 3). The token budget scales with the bound model's window, and the result becomes the cold tier in `memory.assemble`. Tainted and retired rows are never returned. |
| 7.8 | waggle `messages/task/assignment.py`, `ids.py`, `envelope.py`; `queen/ticks/honey.py`, `wardens/ticks/honey.py` and `dispatch.py`, `workers/runtime/honey.py`, `workers/tools/honey.py`, `workers/context.py`, `supervision/attendant/items.py` | The Warden relays `HoneyQuery` and `HoneyResponse` by `InboxItem.correlation_id`. Workers get `recall` (30 s timeout) and `remember`. Hits reach a model only as the delimited `<<<retrieved>>>` untrusted block. |
| 7.9 | `queen/dispatcher/honey.py`, `queen/dispatcher/ready.py`, `queen/planner/plan.py` (`PlanBrief.honey`), `queen/goal_submission.py`, `queen/ticks/results.py` | The planner sees Honey for the goal. Each assignment searches the task's targets, then the chosen Cell's own scope, and attaches the hits to `TaskAssign.honey`, recording `queen.honey_consulted`. Every verified outcome is deposited as TASK_OUTCOME Nectar with `source_key` `task_outcome:<task id>`. |
| 7.9a | `workers/roles/house_bee/honey.py`, `queen/ticks/housekeeping.py`, `honey_store/browse/sources.py` | Cleared and expired wax ripens at `cell:<id>` scope, keeping its severity. The pre-check attaches that history next to the Cell's live wax, and `/cells/<id>/wax` lists the live wax. The Hive Stand's Cell id is now stable (below), so this compounds across processes. |
| 7.10 | `honey_store/scope.py`, `guard/capabilities.py`, `honey_store/browse/` | `HoneyBrowser` is read-only over `/hive`, `/cells/<id>` (with `wax`), `/bees`, `/tasks` and `/bee-bread`. The index folders count by scope in SQL (`scope_counts`), with no scan cap (ADR-0033). A document lists its repeat sources. `propose_note` files a PROPOSED Cell Wax from a Cell's folder; from any other folder it queues a note, which the House Bee drains into HUMAN Nectar. |
| 7.11 | `cli/honey/` (query, browse, maintain, review, context, render, sources), `cli/compose/honey.py`, `cli/stores.py`, `common/logging.py`, `cli/app.py` | `hive honey --manifest M [--clearance C] stats\|query\|ls\|cat\|propose\|relabel\|review\|ripen --now\|reembed [--prune]`. `ripen --now` runs the House Bee's whole pass in-process: notes, ripening, lowerings. `review` lists proposals, and has `approve ID --reason`, `deny ID --reason` and `--judge`. Operator commands record the actor `human`. |
| Lowering (ADR-0034) | `honey_store/lowering/` (rules, state, models, judge, fake, events, review), `store/sqlite/lowering.py`, `llm/prompts/judge_clearance.md`, `cli/compose/honey.py` (`resolve_judge`) | A Nectar whose label only the Real Cell floor holds up, and whose text the Ripener read lower, gets one proposal. The JUDGE slot reviews it (its own rubric, `RUBRIC_ID` `honey-clearance/1`, no shared context), or it waits for the human. The apply transaction re-checks eligibility and reads back. `lowering/README.md` has the flow. |
| Hive Stand identity | `cell/local/source.py` (`HIVE_STAND_SOURCE`, `hive_stand_cell_id`) | The Hive Stand's Cell id is derived from `[hive] node_id`, so every `hive run` on one manifest leases the same Cell id. The `"hive_stand"` source name has one home. |
| Links | `queen/deps.py` (`send_guarded`), `wardens/links.py`, `queen/dispatcher/ready.py`, `workers/runtime/loop.py` | A send over a closed link is logged and returns False, and never crashes a tick. A dispatch whose grant or assignment cannot be delivered revokes the grant (`HOLDER_OFFLINE`) and fails the task. |
| Config | `manifest/schema/honey.py`, `manifest/schema/security.py`, `manifest/schema/llm.py`, `docs/manifests/{minimal,local,full}.toml` | Sections `[honey.store]`, `[honey.ripening]`, `[honey.retrieval]` and `[honey.lowering]` (`enabled`, `max_judge_chars`, `max_proposals_per_pass`, `max_reviews_per_pass`, `max_attempts`), and slots `embedder` and `ripener`. |
| Trail | `pheromone/events/families.py` | `honey.nectar_received`, `nectar_deduplicated`, `nectar_rejected`, `ripened`, `ripen_failed`, `reembedded`, `vectors_pruned`, `queried`, `label_raised`, `label_lowered`, `lowering_proposed`, `lowering_rejected`, `retired`, `note_proposed`, and `queen.honey_consulted`. |
| Drone | `workers/roles/drone/prompt.py` | The reply reserve is `min(4096, 0.25 * window)`, so a small model still has room for Honey. |

## 3. How the pieces meet at runtime

- **Composition.** `cli/compose/honey.build_honey_access` is the only place a `HoneyAccess` is
  built. It bundles the store, intake, retriever, Ripener, clearance judge, identity and the
  config sections, and it resolves EMBEDDER, RIPENER and JUDGE once. Each resolution
  degrades instead of failing: an unusable slot becomes None and is logged once
  (`honey.embedder_unavailable`, `honey.ripener_unavailable`, `honey.judge_unavailable`). Summary
  and embed calls run on a LOW-accuracy Fanner lane. The judge's calls and a query's own embedding
  run on ordinary lanes.
- **Deposits.** Four sources go through `NectarIntake`: the Queen's verified task outcomes, Workers
  (`remember`), the housekeeping sweep's Bee Bread and wax, and operator notes. Intake labels,
  scopes, dedupes and records events in one transaction.
- **Ripening and lowering.** The running Hive does both only in the House Bee loop, and
  `hive honey ripen --now` runs the same pass. Notes drained outside a running Hive are attributed
  to the Hive Stand's stable Cell id. A pass does four things:
  - drain queued operator notes;
  - ripen;
  - file proposals for newly eligible Nectar;
  - review waiting proposals with the judge.
- **Label lowering.** Filing needs three facts: the floor alone holds the label up, the Nectar is
  ripened, and the Ripener's reading is lower. The target is max(declared, reading). The judge
  sees the title, text, kind, media type and target, and never the Ripener's reason, an id or who
  asked. What it cannot answer, or a text longer than `max_judge_chars`, waits for the human in
  `hive honey review`. A lowering holds for later repeats of the same text.
- **Reading.** Every read goes through `HoneyRetriever` with a `HoneyReader`, which carries the
  capabilities, the clearance ceiling and the Night Veil flag.
- **Ranking.**
  - Each text match scores `max(x/(1+x), 0.5 * x/x_best)`, where `x = max(0, -bm25)`.
  - Each vector hit scores `clamp(1 - cosine distance)`.
  - The two are fused with weights 0.4 (text) and 0.6 (vector) and cut at `min_score` 0.15.
  - At most two hits come from one Nectar, and the rest are packed to the token budget.
  - A vector side with no candidates counts as unavailable, and ranking is then text-only.
- **Night Veil.** One deposit from a Night Veil Cell is recorded like ordinary Nectar: the
  RIPENED_HONEY export at C0 or C1. Every other one is EPHEMERAL (no events, purged at teardown),
  and a Night Veil query records no event. Night Veil Nectar is never eligible for lowering.

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

Run the full suite on an idle host. The Hive Stand's capacity probe divides the one-minute load
average by the core count. A busy host (a compile, a local model answering) makes placement
refuse, and e2e cases then fail spuriously.

- **Unit tests** mirror every module under `tests/unit/`. The shared builders are
  `tests/builders/honey.py` (with `make_stand_nectar_draft`), `honey_wire.py`, `house_bee.py` and
  `cli.py`. The prompt snapshots live in `tests/unit/llm/prompts/snapshots/` and
  `tests/unit/honey_store/lowering/snapshots/`, and each directory says how to regenerate them.
- **e2e: `tests/e2e/test_honey_compounds.py`**, with the scripted Hive in `honey_runs.py`:
  - (a) One goal twice on the Hive Stand at C2, at both capability levels. The second
    `TaskAssign` carries the first run's outcome, and the second run takes fewer Drone calls.
  - (b) At C1 with no Ripener reading, nothing is attached.
  - (c) A phase 4 Handoff is found a day later with full provenance.
  - (d) At C1 on default settings, the Ripener reads the outcome as C1 and the judge approves, so
    the second run gets it at C1. The second run's identical outcome deduplicates without raising
    the label back.
  - (e) The judge rejects, nothing is attached, and `hive honey review` lists it REJECTED.
- **e2e: `test_hive_stand_identity.py`**: two builds from one manifest lease the same Hive Stand
  Cell id, and the second reads the first one's `cell:<id>` Honey.
- **e2e: `test_honey_wire.py`**: a Worker's `recall` and a two-chunk deposit, over real Waggle.
- **Evals (`local_llm`)** in `tests/evals/honey/`, documented in `tests/evals/README.md`. Both
  skip unless `HIVEMIND_LIVE_LLM=1` and `HIVEMIND_LOCAL_LLM_BASE_URL` are set.
  - `test_ripening_local.py` is exit criterion 3, in two cases: RIPENER and EMBEDDER on the server,
    and EMBEDDER in process through sentence-transformers.
  - `test_clearance_judge_local.py` asks the judge about six texts: two it must approve and four
    it must reject.
- Commit only behind green gates.

## 5. Running it for real

The scripts below lived in the second session's scratchpad, which is gone. Rebuild them from these
descriptions.

- **Models.** Docker Hub's `ai/` namespace serves models as OCI artifacts, anonymously:
  - `ai/qwen2.5:3B-Q4_K_M` and `ai/qwen2.5:7B-Q4_K_M`;
  - `ai/nomic-embed-text-v1.5:137M-F16`;
  - `ai/qwen3-embedding:0.6b-safetensors`, a sentence-transformers directory.

  To fetch one, get a token from
  `https://auth.docker.io/token?service=registry.docker.io&scope=repository:<repo>:pull`, then GET
  `https://registry-1.docker.io/v2/<repo>/manifests/<tag>` with an OCI manifest `Accept` header,
  then GET the largest `gguf` layer's blob. For the safetensors directory, fetch every layer and
  name each file from its `org.cncf.model.file.metadata+json` annotation. Two things go wrong:
  - Anonymous pulls are rate-limited, and a 429 needs a back-off.
  - Long transfers get cut. Resume with `Range: bytes=<have>-` and check the file's sha256 against
    the layer digest before using it. A 4.7 GB blob once stopped 1.4 MB short and "finished" with
    exit 0.
- **Model server.** Make a venv with `llama-cpp-python[server]==0.3.35`. It compiles from source
  for several minutes, and the compile's CPU load fails e2e runs.
  - Start it with `python -m llama_cpp.server --config_file server.json`.
  - The config lists one entry per model: `model` (the path), `model_alias`, `n_ctx` 8192,
    `n_threads` 4, and `embedding: true` for nomic.
  - It swaps models per request.
  - It supports only `response_format` `json_object`, so the provider's capabilities are
    `schema_output = false` and `json_mode = true`.
  - Stop it by PID (`ps -eo pid,args | grep "[l]lama_cpp.server"`). **Never use `pkill -f`**: the
    pattern matches your own shell's command line and kills it.
- **In-process embedder.** Make a separate venv with the workspace plus the `embeddings` extra
  (torch, CPU). Run the eval's second case with `HIVEMIND_LOCAL_ST_EMBED_MODEL` set to the model
  directory.
- **Manifest for real runs.** Start from `tests/builders/cli.fake_manifest(root)`.
  - Add a provider `local` with `kind = "openai_compat"`, `base_url = "http://127.0.0.1:8765/v1"`
    and the capabilities above.
  - Rebind `ripener` to the 3B, `judge` to the 7B and `embedder` to nomic.
  - Set only `[honey.ripening] interval_s = 2.0`. The default `summarise_min_chars` no longer
    stops a short outcome from being read.
- **Multi-process driver** (`p7_real.py`, about 300 lines).
  - `prepare ROOT` writes the manifest.
  - `run ROOT --clearance C1 --label L` is one `hive run`-sized process. It calls
    `build_hive(load_manifest(path, {}), environ={}, clock=SystemClock(),
    responders={"fake": brain.responder})` outside any event loop, then
    `run_goal(hive, GOAL, clearance=HoneyClearance.C1, timeout_s=300)` inside `run_hive(hive)`.
  - It waits for the outcome deposit, then until the House Bee's own loop has ripened and embedded
    it and no proposal is still `PROPOSED`. It never calls a pass by hand.
  - It prints JSON: Drone calls, the hits in each captured `TaskAssign` (captured by wrapping
    `Codec.encode`), Honey rows, proposals, trail counts, `llm.call` counts by slot and the Hive
    Stand Cell ids leased.
  - The slot is a top-level field of an `llm.call` event, not a payload key.

  The scripted brain plans one task (`FILE_EXISTS answer.txt`) whose Drone discovers a port with
  a real subprocess, unless the `<<<retrieved>>>` block already states the port. A run at C1 takes
  about two and a half minutes, almost all of it model time.
- **Judge probes.** Before a rubric or model change, run `test_clearance_judge_local.py` with
  `HIVEMIND_LOCAL_JUDGE_MODEL` set. Also ask the judge several times about a real stored outcome,
  because a single sample at the server's default temperature proves little.
- **CLI walk.** Run against a real run's manifest (options come **before** the subcommand):
  - `hive honey --manifest M stats`
  - `hive honey --manifest M review`
  - `hive honey --manifest M ls /cells`
  - `hive honey --manifest M cat <path>`
  - `hive honey --manifest M query "…"`
  - `hive honey --manifest M propose /hive "title" "text"`, then `ripen --now`
  - `hive honey --manifest M reembed [--prune]`

## 6. What the real runs proved (2026-09-24)

- **First session, stand-in models** (WordLlama vectors and an extractive chat shim at C2):
  - Compounding worked on the Hive Stand: 3 Drone calls, then 2.
  - Every fallback degraded without raising.
  - Operator notes drained into Honey.
  - An embedder switch kept the old and new vectors side by side.
  - Every `hive honey` command worked.
- **`local_llm` evals on real models.**
  - `test_ripening_local.py` passed both cases: qwen2.5-3B plus nomic on the server, and
    Qwen3-Embedding 0.6B in process.
  - `test_clearance_judge_local.py` passed 6 of 6 on qwen2.5-7B.
  - On qwen2.5-3B it failed both approvals.
- **First attempt at C1: JUDGE on the Ripener's own 3B model.**
  - The 3B Ripener read the 282-byte outcome as C1, and the House Bee filed a C2 to C1 proposal.
  - The 3B judge rejected it. Probed five more times, it rejected it five times, with reasons such
    as "a port number is a credential or secret" and a person's name found in the title. It also
    rejected a harmless build log.
  - Rewriting the rubric (a narrower C2, plus a list of what is not sensitive) did not help the
    3B. It made a 7B approve a private IP address with a machine name as "internal". That edit was
    reverted.
  - Under the shipped rubric the 7B decided every probe correctly. ADR-0034 already advised a
    different judge model from the Ripener's.
- **Second attempt, JUDGE on the 7B.** The judge approved, and the Nectar and its Honey rows went
  from C2 to C1. The second process leased the same Hive Stand Cell id and got the outcome at C1
  in its `TaskAssign` (2 hits). Its Drone went from 4 calls to 2.
- **The bug that run found.** The second run's outcome was the same text. It deduplicated onto the
  lowered Nectar, and the merge raised it back to C2 (`honey.label_raised`). With one proposal per
  Nectar it could never come down again, so C1 compounding would have lasted one run. Fixed in
  `240d388`.
- **Third attempt, three processes on the fixed code** (fresh root). Run 1 discovered the port in 4
  Drone calls, and the judge lowered its outcome to C1. Runs 2 and 3 each got it at C1 (2 hits in
  each `TaskAssign`) and needed 2 calls. Their identical outcomes merged as two repeat sources, and
  no `honey.label_raised` event was recorded at all. All three runs leased the same Hive Stand Cell
  id. Run 1's metered calls were QUEEN 1 and WORKER 4 (scripted), plus RIPENER 1, EMBEDDER 4 and
  JUDGE 1 (real, local). Runs 2 and 3 each made QUEEN 1, WORKER 2 and EMBEDDER 3 (their queries),
  and no RIPENER or JUDGE call, since their outcomes merged onto the ripened Nectar.
- **CLI walk on that database.** Every command worked:
  - `stats`;
  - `review`, which showed the LOWERED proposal with the Ripener's reason and the judge's rubric id;
  - `ls /hive`, and `cat` with both repeat sources;
  - a `C1` `query`, which found the outcome and withheld a C2 note;
  - `propose` then `ripen --now`, which drained the note into HUMAN Nectar at C2 and proposed
    nothing, because HUMAN is human-only;
  - an embedder switch to Qwen3-Embedding in process (the `embeddings` venv), where
    `reembed --prune` re-embedded all three rows and dropped nomic's three vectors, and a `C1` query
    still found the outcome with vectors in use.
  - It found one more bug. A repeat source recorded its floor-raised label, and `cat` printed it as
    `declared=C2` for runs made at C1. It now records what it declared (`e9a3ab2`).

## 7. Defects found and fixed

The commit bodies have the details.

**First session:**

- `dc5bb64`: on a young store, full-text search found nothing, because bm25 weighted every word at
  about 1e-6. The fix is the relative text floor, and `min_score` went to 0.15.
- `70c022e`: the Drone's fixed 4096-token reply reserve starved an 8k model of Honey.
- `f463838`: CLI logs corrupted `--json`. They now go to stderr.
- `feefc23`: `withheld` counted rows the query's own scope narrowing left out.
- `93e58f4`: after an embedder switch, an empty vector side still took its fusion weight.
- `8b189c5`: the Python vector fallback was 2.5x slow. It now uses `math.sumprod`.
- Night Veil, label, embedding and store defects from subagent reviews were folded into `d3be476`,
  `d0b84d2` and `3052cce`.

**Second session:**

- `8be161c`, `b517dac`: the Hive Stand's Cell id changed on every process. The `"hive_stand"`
  literal lived in three places.
- `53508f7`: `ripen --now` did not drain notes.
- `84c6c8f`: identical outcomes lost the second task's provenance. The index folders scanned at
  most 5,000 rows. Old-model vectors were never pruned. (ADR-0033.)
- `05b1fa1`, `c179549`, `cbe09a3`: a send over a closed link crashed a Warden or Queen tick, or a
  Worker's Alarm flush. An undeliverable dispatch recorded `forage.denied` for a live grant; it now
  revokes the grant with `HOLDER_OFFLINE`.
- `9285cb1`: text-only chat content sent as a part array made llama.cpp return 500.
- `5cb6c66`, `98fea3c`: pooled HTTP connections were never closed, and warnings-as-errors failed
  the eval session.
- `ab03c8a`: sentence-transformers 5.x deprecated the dimension call the adapter used.
- `87eadb2` through `453815f`: judge-reviewed lowering (ADR-0034), including the Ripener's honest
  reading.
- `c24776d`: an outcome under `summarise_min_chars` never got a reading, so on default settings
  nothing on the Hive Stand could be lowered.
- `240d388`: a repeat of a lowered text raised it back to C2 for good.
- `27589d6`: the clearance judge eval, after a 3B judge proved useless.
- `e9a3ab2`: a repeat source recorded its floor-raised label where ADR-0033 says the declared one.

## 8. Open items, in suggested order

1. **Re-run exit criterion 1 with the real Forager** once phase 6 lands, and re-tick with a note.
2. **The judge's model is the operator's choice, and a weak one silently blocks lowering.**
   `local.toml` binds every slot to one model and now says why the judge should differ. A check
   that warns when JUDGE and RIPENER resolve to the same model would belong with phase 8's routing.
   So would running the judge eval per candidate model.
3. **Verdict reasons can quote the text.** The 3B judge copied an API key into its reason despite
   the hard rule. Reasons live only on the proposal row and in `hive honey review`, never on the
   trail. Treat them as C2 wherever a later phase shows them (the Entrance's `honey` route, the
   Observation Hive).
4. **Per-embedder calibration.** `min_score` and the fusion weights were tuned on one embedder;
   each needs its own (phase 8's eval). The Python vector fallback stays linear, at 208 ms for 5k
   rows of 768 dimensions.
5. **Link findings from the send guard** (not fixed, out of phase 7's scope):
   - The Brood Chamber's `unassign` edge has no production caller.
   - There is no Warden outbox. A send that fails on a closed link is logged and dropped.
   - The Queen does not re-send a ceiling or a hosting plan after a failed first delivery.
6. **Size watch.** `pheromone/events/families.py` is at 295 of 300 lines of code, and
   `cli/honey/render.py` at 248. The next event family or renderer needs a split first.
7. **`ripen --now` drains at most 1,000 waiting notes** (`_WAITING_NOTES_LIMIT`) and does not say
   when it hit that cap.

## 9. Traps

- `build_hive` must be called **outside** a running event loop; it uses `asyncio.run` internally.
- The Drone's prompt text mentions the `<<<retrieved>>>` delimiter itself. Find the real block
  from its end marker (`rfind("<<<end retrieved>>>")`).
- click 8.5 mixes stderr into `result.output`, so assert on `result.stdout` in CLI tests. Loggers
  write through `_CurrentStderr`.
- `hive honey` options come **before** the subcommand.
- A trail event's `subject_id` must start with a known waggle `IdKind` prefix.
- A C2 hit from the Hive Stand is correct until the judge lowers it. Do not "fix" it.
- Judge quality is the model's, not the rubric's. Change `judge_clearance.md` only behind the
  judge eval, and bump `RUBRIC_ID` when you do. A friendlier rubric made a 7B approve an IP address.
- The e2e suite is load-sensitive (section 4). So is a real run: the drivers wait for the load
  average to fall below 1.2 before each process.
- A model download that "finishes" can still be short. Always check the digest.
- A `cd` inside a compound shell command moves the session's working directory. Start compound
  commands with `cd /home/user/Hivemind`.

## 10. Next steps

Phase 8 (local models and provider routing) is next in the roadmap. Items 2 and 4 belong there:
judge-model routing and per-embedder calibration. Phase 6 remains unbuilt. If the operator wants
the roadmap order restored, build phase 6 next and then re-run item 1.
