# hivemind evals

Handoff and model evaluation harnesses: reports written by `hive llm eval` that score how well a
routing choice or a Handoff (a resumable snapshot of a task's memory) held up. First populated in
phase 4 (Clustering, for Handoffs) and extended in phase 8 (local models and provider routing).

## `handoff/`: roadmap step 4.5, the Handoff quality eval

Stops a Drone mid-task after two of four scratch-file writes (through the runtime's own handoff
mechanism -- see `handoff/scenario.py`'s module docstring), checkpoints it, and hands a brand-new
Worker nothing but the stored Handoff. Grades three things (`handoff/grader.py`):

- **completion** -- every expected effect (a scratch file) exists once the second bee finishes.
- **no-redo** -- no tool call the fake session recorded *after* the checkpoint repeats a step the
  Handoff's own `do_not_redo` names.
- **Handoff shape** -- the mandatory fields a real checkpoint always fills (`goal`, `progress`,
  `written_by`, `decisions`, `next_steps`) hold real content, not just an empty tuple the schema
  happens to allow.

### Running it

```bash
# Fake provider (fast, no marker beyond the normal unit run's own exclusions; runs in CI):
uv run pytest packages/hivemind/tests/evals/handoff -m "not live_llm and not local_llm"

# Real hosted provider (docs/manifests/minimal.toml), opt-in only:
HIVEMIND_LIVE_LLM=1 HIVEMIND_ANTHROPIC_API_KEY=... \
  uv run pytest -m live_llm packages/hivemind/tests/evals/handoff

# Real local server (docs/manifests/local.toml), opt-in only:
HIVEMIND_LIVE_LLM=1 HIVEMIND_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  uv run pytest -m local_llm packages/hivemind/tests/evals/handoff
```

The `live_llm`/`local_llm` variants are gated exactly like `tests/contracts/
test_llm_provider_contract.py`'s own `live_llm` suite, and skip cleanly when unset. They grade
completion, Handoff shape and no-redo, all strictly: `hivemind.workers.roles.drone.sources.
DroneSources.handoff` surfaces the whole resumed Handoff -- `do_not_redo` rendered as an explicit
instruction list included -- into a resuming bee's own prompt.

Set `HIVEMIND_EVAL_REPORT_DIR` to also write each run's `HandoffEvalReport` as JSON under
`docs/evals/` (see that directory's own README); unset (the default, and always the case in CI),
`grader.build_report` still returns the report, only nothing is written to disk.

## `honey/`: roadmap phase 7, ripening on local models

`test_ripening_local.py` is phase 7's third exit criterion: Nectar ripens into Honey with the
`RIPENER` and `EMBEDDER` slots on a local OpenAI-compatible server, bound from
`docs/manifests/local.toml` through the same `hivemind.cli.compose.honey.build_honey_access` a
running Hive uses. One finding is deposited; one ripening pass must summarise it on the model
(the SUMMARY row records its ripener model), embed every row, and a paraphrased query must find it
with vectors in the ranking.

```bash
HIVEMIND_LIVE_LLM=1 HIVEMIND_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  uv run pytest -m local_llm packages/hivemind/tests/evals/honey
```

`HIVEMIND_LOCAL_RIPENER_MODEL` and `HIVEMIND_LOCAL_EMBED_MODEL` replace the two slots' model ids
for a server that hosts different ones than the manifest names. Like the handoff eval it skips
cleanly when unset, and nothing about it runs in CI.

A second case, `test_ripening_embeds_in_process_with_sentence_transformers`, runs the same pass
with `EMBEDDER` on the in-process `sentence_transformers` adapter instead of the server (`RIPENER`
stays on the server). It runs when `HIVEMIND_LOCAL_ST_EMBED_MODEL` names a model, as a
sentence-transformers name or a local directory, and the `embeddings` extra is installed
(`uv sync --extra embeddings`). The manifest is offline, so the model loads with
`local_files_only` and never reaches a model hub: download it first.

Both cases passed on 2026-09-24 against llama.cpp's OpenAI-compatible server
(`llama-cpp-python[server]`) serving qwen2.5-3B-Instruct (Q4_K_M) for `RIPENER` and
nomic-embed-text v1.5 for `EMBEDDER`, with Qwen3-Embedding 0.6B in process for the second case.
Those runs found and fixed three adapter problems no fake could show:

- A text-only message sent as a part array made llama.cpp return a 500.
- HTTP clients that were never closed failed the session under warnings-as-errors.
- sentence-transformers 5.x warns on `get_sentence_embedding_dimension`.

### `test_clearance_judge_local.py`: the clearance judge on a local model

ADR-0038's clearance judge (`ModelClearanceJudge` on the `JUDGE` slot, the shipped
`judge_clearance.md` rubric) is asked, at a `C1` target, about six texts. It must approve two: a
verified Hive Stand outcome naming a service's port, and a build log. It must reject four: a
person's name, a private address with a machine name, a home path that names a person, and an API
key. The binding comes from `docs/manifests/local.toml` through the production registry and
`resolve_judge`.

```bash
HIVEMIND_LIVE_LLM=1 HIVEMIND_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  HIVEMIND_LOCAL_JUDGE_MODEL=<the judge model> \
  uv run pytest -m local_llm packages/hivemind/tests/evals/honey/test_clearance_judge_local.py
```

Run it against any model meant for the `JUDGE` slot and before and after any change to the rubric.
Each case is one sampled answer at the server's default temperature, so a single wrong answer is a
signal to re-run and look at the reasons, not proof either way.

On 2026-09-24, against the same llama.cpp server, qwen2.5-7B-Instruct (Q4_K_M) passed all six
cases. qwen2.5-3B-Instruct (Q4_K_M) rejected everything, so it failed both texts it must approve,
the build log included. Bind the judge to a different, stronger model than the Ripener.

## `routing/`: roadmap step 8.5, the evaluation harness

`hive llm eval` (`hivemind.cli.llm.eval`) runs a fixed goal set against the manifest's bindings and
grades decomposition, `TaskNeeds`, tool-call validity, ladder rung, Alarm rate, handoff-resume,
steps, cost and wall time, then maps the scores to measured grades (docs/evals/README.md explains
every column and the mapping).

`test_eval_harness.py` runs the whole pipeline on the fake provider (fast, runs in CI): a real Hive
per goal over a `builders.cli.fake_manifest`, scripted by `builders.evals.ScriptedEval` so each of
the four goals behaves. It asserts that every goal passes and exercises the behaviour it measures
(the question asked and answered, the checkpoint resumed), that the report is written as markdown
and JSON with a column for every grader, that `--update-map` writes grades through the Hive's own
ledger which the *next* Hive's Forage map applies (`effective_grade` changes while the manifest's
`grade` does not), that a Drone doing nothing fails its goal and earns a lower grade, and that the
TRANSCRIBER is graded on a fixture clip set. The clips are generated tones graded through
`FakeTranscription`, which proves the grader; real clips are the operator's to supply.

```bash
uv run pytest packages/hivemind/tests/evals/routing -m "not live_llm and not local_llm"
```

`test_eval_harness_local.py` is the `local_llm` variant, gated like the honey evals: it runs the
harness against `docs/manifests/local.toml` pointed at a real server, and passes when every goal
reached a verdict without crashing (whether a small model passes the goals is the result, not the
assertion).

```bash
HIVEMIND_LIVE_LLM=1 HIVEMIND_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  uv run pytest -m local_llm packages/hivemind/tests/evals/routing
```

`HIVEMIND_LOCAL_EVAL_GOALS` (comma-separated, default `haiku`) picks the goals;
`HIVEMIND_LOCAL_QUEEN_MODEL` and `HIVEMIND_LOCAL_WORKER_MODEL` replace the two scored slots' model
ids; `HIVEMIND_EVAL_REPORT_DIR` also writes the report there. Unset, it skips cleanly and nothing
about it runs in CI.

## `offline/`: roadmap steps 8.6 and 8.7, offline mode on a real model

The scripted counterpart is `tests/e2e/offline/` (it runs in CI on every push); these are the same
claims with a real model behind the socket, derived from `docs/manifests/offline.toml`
(`tests/e2e/offline/live.py`) and run under the same no-egress socket guard, so a connect to
anything but loopback fails the test. Quality is not asserted (`hive llm eval` grades that): each
case asserts that the goal reached a verdict without timing out, that every `llm.call` named a
provider that runs on this machine, and its own claim.

- `test_the_haiku_goal_runs_offline_on_a_real_local_model` -- the Queen, Drone and Judge on the
  real server.
- `test_a_spoken_goal_is_heard_by_in_process_whisper_and_runs` -- a WAV clip transcribed by
  `whisper_local` (the `hivemind[whisper]` extra, a model already cached) and run as the goal; skips
  without `HIVEMIND_LOCAL_SPEECH_CLIP` or the extra.
- `test_the_forager_reads_the_page_by_its_accessibility_tree_on_a_real_local_model` -- the real
  model as WORKER, with the offline file's `vision = false`, logging in to the fixture site; a
  scripted loopback server plans and judges so the plan is exact.

```bash
HIVEMIND_LIVE_LLM=1 HIVEMIND_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  HIVEMIND_LOCAL_LARGE_MODEL=<queen/judge/warden model> HIVEMIND_LOCAL_SMALL_MODEL=<the rest> \
  uv run pytest -m local_llm packages/hivemind/tests/evals/offline
```

`HIVEMIND_LOCAL_WHISPER_MODEL` replaces the Whisper size, `HIVEMIND_LOCAL_EMBED_MODEL` serves the
embedder from the chat server instead of in process, and `HIVEMIND_LOCAL_GOAL_TIMEOUT_S` (default
900) bounds each goal. They skip cleanly when unset, and `.github/workflows/integration.yml`'s
`local_llm` job runs them nightly on an Ollama it installs on the runner.
