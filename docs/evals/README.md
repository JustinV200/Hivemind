# Eval reports

Reports written by `hive llm eval`, scoring how well a model, a routing choice, or a Handoff (a
resumable snapshot of a task's memory) performed. Populated once the eval harnesses under
`packages/hivemind/tests/evals/` exist, from phase 4 onward.

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
