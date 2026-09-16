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
completion and Handoff shape strictly; they do not hard-assert no-redo (`handoff/scenario.py`'s
`run_live_handoff_scenario` docstring explains the real production gap that makes doing so
unreliable: a resumed Handoff's `do_not_redo` never reaches a real model's own prompt today).

Set `HIVEMIND_EVAL_REPORT_DIR` to also write each run's `HandoffEvalReport` as JSON under
`docs/evals/` (see that directory's own README); unset (the default, and always the case in CI),
`grader.build_report` still returns the report, only nothing is written to disk.
