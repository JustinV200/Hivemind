"""Provide `hive llm eval`: the evaluation harness that grades the manifest's model bindings.

Roadmap step 8.5. A fixed set of goals (`goals`) is run, one fresh Hive each (`runner`), against
whatever the manifest binds; every run's trail and scratch files are scored on nine columns
(`graders`, models in `grades`, raw data in `evidence`) and on the TRANSCRIBER slot by word error
rate (`transcriber`); slot scores become measured grades 1 to 5 (`measured`) that `--update-map`
writes through the Queen's ledger so the next Hive routes on evidence rather than hand-set grades;
`pipeline` ties the steps together, `report` writes `<date>.md` and `<date>.json`, and `commands`
is the typer command. This face exports the pieces an operator's script or a test composes.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Registered on `hive llm` by
    `hivemind.cli.llm.commands`. Calls into every layer below through the Hive it builds.

Key invariants:
    - Grading is pure and separate from running: `graders`, `measured` and `report` never start a
      Hive, so each is tested without one.
    - The harness never writes the manifest and never touches the operator's own Hive database
      except for the measured-grade rows `--update-map` asks for.

See Also:
    - docs/evals/README.md for the report and how to run it.
    - docs/adr/0045-provider-routing-forage-aware-fallbacks-and-offline-mode.md for measured grades.

Public API:
    - GOAL_SET, EvalGoal, select_goals: the fixed goal set (goals).
    - run_evaluation, EvalRequest: run, grade, score and report (pipeline).
    - EvalReport, write_report, render_markdown: the report (report).
    - grade_for_score, score_slots, assign_grades: scores to measured grades (measured).
    - wer, grade_clips: word error rate and the clip-set runner (transcriber).
    - register_eval_command: attach `eval` to the `hive llm` group (commands).
"""

from hivemind.cli.llm.eval.commands import register_eval_command
from hivemind.cli.llm.eval.goals import GOAL_SET, EvalGoal, select_goals
from hivemind.cli.llm.eval.measured import assign_grades, grade_for_score, score_slots
from hivemind.cli.llm.eval.pipeline import EvalRequest, run_evaluation
from hivemind.cli.llm.eval.report import EvalReport, render_markdown, write_report
from hivemind.cli.llm.eval.transcriber import grade_clips, wer

__all__ = [
    "GOAL_SET",
    "EvalGoal",
    "EvalReport",
    "EvalRequest",
    "assign_grades",
    "grade_clips",
    "grade_for_score",
    "register_eval_command",
    "render_markdown",
    "run_evaluation",
    "score_slots",
    "select_goals",
    "wer",
    "write_report",
]
