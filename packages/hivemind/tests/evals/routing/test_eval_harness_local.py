"""local_llm: the evaluation harness grades a real local model on the fixed goals.

Roadmap step 8.5 asks the harness to ground the Forage map's grades in the Hive's own results, and
only a real model shows whether the goals, the graders and the scoring mean anything. This runs
`run_evaluation` against `docs/manifests/local.toml` pointed at the operator's own OpenAI-compatible
server (`HIVEMIND_LOCAL_LLM_BASE_URL`): each selected goal gets a fresh Hive, a real Queen plan, a
real Drone and a real Hive Stand Cell. The assertions are deliberately loose about the *quality*
(a small model may well fail a goal, and the report saying so is the harness working) and strict
about the *harness*: every goal ran to a verdict without a crash, and every grader column holds a
value.

Gated like `tests.evals.honey.test_ripening_local`: skips cleanly unless `HIVEMIND_LIVE_LLM=1` and
`HIVEMIND_LOCAL_LLM_BASE_URL` are set. `HIVEMIND_LOCAL_EVAL_GOALS` (comma-separated, default
`haiku`) picks the goals, `HIVEMIND_LOCAL_QUEEN_MODEL` and `HIVEMIND_LOCAL_WORKER_MODEL` replace
the two scored slots' model ids for a server that hosts different ones, and
`HIVEMIND_EVAL_REPORT_DIR` also writes the report there (as the handoff eval does); unset, nothing
is written. No model id or URL is written in this file (`scripts/check_no_model_ids.py`).

Fits into the Hive:
    Test infrastructure (codingrules section 14.2), not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - tests/evals/README.md for how to run it, and docs/evals/README.md for the report.
    - tests/evals/routing/test_eval_harness.py for the same pipeline over a scripted fake.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from builders.evals import local_eval_manifest

from hivemind.cli.llm.eval import EvalRequest, render_markdown, run_evaluation, select_goals
from hivemind.cli.llm.eval.report import GOAL_COLUMNS, write_report
from hivemind.cli.llm.eval.runner import RunnerOptions
from waggle.clock import SystemClock

_REPORT_DIR_ENV_VAR = "HIVEMIND_EVAL_REPORT_DIR"  # Shared with tests.evals.handoff.grader.
_DEFAULT_GOALS = "haiku"  # One goal keeps a first run on a small model to a few minutes.


@pytest.mark.local_llm
def test_the_harness_grades_a_local_model_on_the_fixed_goals(tmp_path: Path) -> None:
    """Skips cleanly unless HIVEMIND_LIVE_LLM=1 and a local server base URL are set."""
    if os.environ.get("HIVEMIND_LIVE_LLM") != "1":
        pytest.skip("HIVEMIND_LIVE_LLM is not set to '1'.")
    if not os.environ.get("HIVEMIND_LOCAL_LLM_BASE_URL"):
        pytest.skip("HIVEMIND_LOCAL_LLM_BASE_URL must be set.")
    manifest = local_eval_manifest(os.environ)
    names = tuple(os.environ.get("HIVEMIND_LOCAL_EVAL_GOALS", _DEFAULT_GOALS).split(","))
    options = RunnerOptions(os.environ, SystemClock(), tmp_path / "work")

    report = run_evaluation(EvalRequest(manifest, "local.toml", select_goals(names), options))

    report_dir = os.environ.get(_REPORT_DIR_ENV_VAR)
    write_report(report, Path(report_dir) if report_dir else tmp_path / "reports")
    # The harness ran every goal to a verdict; whether a small model passed it is the result.
    assert [result.goal for result in report.goals] == [n.strip() for n in names]
    assert all(result.error is None for result in report.goals), [r.error for r in report.goals]
    markdown = render_markdown(report)
    for column in GOAL_COLUMNS:
        assert column in markdown
