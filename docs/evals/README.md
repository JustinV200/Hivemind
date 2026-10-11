# Eval reports

Reports written by `hive llm eval`, scoring how well a model, a routing choice, or a Handoff (a
resumable snapshot of a task's memory) performed. Populated by the harnesses under
`packages/hivemind/tests/evals/` from phase 4 (the Handoff eval) and by `hive llm eval` from phase 8
(model evaluation, below).

## Handoff quality eval (roadmap step 4.5)

`packages/hivemind/tests/evals/handoff/` scores a Drone that checkpoints mid-task and a fresh
Worker that resumes from nothing but the stored Handoff, on completion, on not repeating a
`do_not_redo` step, and on the Handoff's own mandatory fields holding real content -- see that
package's own README for how to run each variant.

A run writes its `HandoffEvalReport` here as `handoff-<scenario>.json` only when the operator (or
a script) sets `HIVEMIND_EVAL_REPORT_DIR=docs/evals`; CI never sets it, so no report lands in the
repository from an ordinary test run. Each file holds three grades (`completion`, `no_redo`,
`handoff_shape`, each with its own `passed` flag and the specifics behind it) plus one overall
`passed`; the `scenario` field names which variant produced it (`fake-positive`, `fake-negative`,
`live-hosted`, `local`, ...).

## Model evaluation (roadmap step 8.5): `hive llm eval`

`hive llm eval` runs a fixed set of goals against whatever the manifest binds and grades the
result, so a model is chosen, and the Forage map's grades are set, from the Hive's own results
rather than a guess. Each goal runs on its own fresh Hive (a private database and scratch
directory, so the operator's own Hive is untouched), with a real Queen plan, a real Drone and a
real Cell, and a small attendant beside it playing the human: it answers the one question a goal
provokes and forces the one checkpoint a goal wants.

```bash
hive llm eval --manifest docs/manifests/local.toml                  # all goals, report to docs/evals
hive llm eval --manifest hive.toml --goals haiku,ask --report-dir /tmp/evals
hive llm eval --manifest hive.toml --update-map                     # also write measured grades
hive llm eval --manifest hive.toml --fixtures clips/                # also grade the TRANSCRIBER
```

The goals (`hivemind.cli.llm.eval.goals`): `haiku` (plan a small task, write three files),
`command` (run one command and report what it printed), `ask` (stop to ask a question and carry on
once it is answered) and `checkpoint` (be checkpointed mid-task and resume from the Handoff alone).

A run writes `<date>.md` and `<date>.json` under `--report-dir` (default `docs/evals`; the date is
the file name, so a second run on the same day replaces the first). The JSON is the contract, the
markdown is for reading. The goal table has one row per goal and one column per grader:

| Column | What it grades |
|---|---|
| Result | PASS needs a clean run that succeeded with every applicable column passing; ERROR means the run itself could not finish. |
| Decomposition | A plan was recorded, it made tasks within the goal's bound, and each task has acceptance criteria a Warden can check. |
| TaskNeeds | The planned tasks declared the needs a sound plan would (no display, audio, network or raised tier for these goals), as the fraction of matching fields. |
| Effects | The expected files exist in scratch, with the expected content. |
| Ask | The goal that must ask did: a task blocked on a question and was answered. `n/a` for the others. |
| Tool-call validity | One minus the ladder rungs that ran out of retries on malformed output (`llm.fallback`, reason `RUNG_EXHAUSTED`) over model calls (`llm.call`). |
| Rung | The structured-output rung the planner's model starts on (from its declared capabilities), and the weakest rung any call stepped down to. |
| Alarm rate | Alarms raised per planned task. Reported, not pass/fail. |
| Handoff | Every checkpoint was resumed and the goal still finished. `n/a` unless a checkpoint was forced or happened. |
| Steps, Cost, Wall time | Worker model calls, the sum of priced `llm.call` cost (calls with no price are counted beside it, never read as free), and seconds from submission to the end. |

### Measured grades (`--update-map`)

Each scored slot gets a 0 to 1 score: the QUEEN from her plans (valid, and sound `TaskNeeds`), the
WORKER from whether the work landed, how often its output was well-formed and whether it answered
and resumed when asked, the TRANSCRIBER from word error rate. A slot is scored only if it made
calls. The score becomes a grade with `grade = min(5, 1 + floor(score * 5))`: below 0.2 is 1, then
0.2, 0.4, 0.6 and 0.8 start grades 2 to 5. The grade goes to the Forage map source the slot's
primary binding names; a source serving several scored slots takes the lowest. With `--update-map`
each is written to the Queen's ledger (`forage_ledger_measured_grades`, in the manifest's own
`[hive] db`), and `build_hive` applies them over the manifest's hand-set grades when it builds the
next Hive's map (`ModelSource.effective_grade`), which is what routing's floors, allocation and the
spill rule read. The manifest file is never rewritten; delete a row to fall back to the hand-set
grade. A run in which a slot made no calls, because the Hive could not start, grades nothing.

### Transcriber (`--fixtures DIR`)

`DIR` holds `<clip>.wav` files, each beside a `<clip>.txt` with exactly what was said. Each clip
goes through the slot's resolved transcriber; the report gives per-clip word error rate (the
word-level edit distance over the reference length, lower-cased and without punctuation),
latency and real-time factor, and their means. The clip set this repository tests with is
synthetic: generated tones, graded through the fake transcriber so the grader itself is proven.
Real clips of real speech are the operator's to supply; run the command once per candidate model
(each Whisper size in turn) and read the reports to pick the smallest that clears the bar.

### Reading a run against a scripted server

`packages/hivemind/tests/e2e/scripted_openai.py` is a scripted OpenAI-compatible server on loopback.
With a scenario of `{"files": ["haiku_1.txt", "haiku_2.txt", "haiku_3.txt"]}` it lets the whole
command run over the real adapter and real sockets with no model; the `command` goal fails there,
as it should, because the server only ever writes the haiku files.
