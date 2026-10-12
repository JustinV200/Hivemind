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

## `scaffold/`: roadmap phase 9, the SCAFFOLDER on a local model

`test_scaffold_local.py` is phase 9's fourth exit criterion: the scaffold scenario passes with the
`SCAFFOLDER` slot on a local model. `scenario.py` is the first exit criterion's flow cut to a
Drone, in a whole Hive built with `build_hive`: a Worker writes `data/notes.txt`, asks for a line
counter with `request_tool`, the Queen's Royal Jelly flow has the `SCAFFOLDER` write it, the real
Quarantine Comb proves it (the development subprocess sandbox: this scenario needs no Docker),
the Queen promotes it, and the Worker calls it by the argument its own schema names and finishes
with the count. The planner, the Worker and the judge stay on the fake provider, so the only
variable is who writes the tool; `with_local_scaffolder` rebinds that one slot, its Forage source
and its model to the OpenAI-compatible server `docs/manifests/local.toml` describes. The case
passes when the tool is promoted and the Worker's task finishes with the right count; a model that
writes a tool the Comb rejects fails it with the failing gate named, which is the finding.

```bash
HIVEMIND_LIVE_LLM=1 HIVEMIND_LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1 \
  HIVEMIND_LOCAL_SCAFFOLDER_MODEL=<the scaffolder model> \
  uv run pytest -m local_llm packages/hivemind/tests/evals/scaffold
```

`HIVEMIND_LOCAL_SCAFFOLDER_MODEL` replaces the model id local.toml names for the slot. Like the
others it skips cleanly when `HIVEMIND_LIVE_LLM` or the base URL is unset. Two cases run in CI with
no server: the same scenario with a scripted fake `SCAFFOLDER` (so the scenario, its grader and
the wiring are exercised on every run), and a check that the local binding moves only the
`scaffolder` slot and resolves. The local case was exercised once against a stand-in loopback
server that answers with the scripted tool; it has not been run against a real model, so a real
model's first run may find prompt or schema problems no fake can show. One to expect: a model that
declares a bare wildcard such as `fs:read:**` writes a tool that passes the Comb but is refused by
the Guard whenever a Worker calls it (the prompt now says to name a directory).
