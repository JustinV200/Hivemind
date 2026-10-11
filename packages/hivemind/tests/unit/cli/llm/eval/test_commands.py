"""Tests for hivemind.cli.llm.eval.commands: `hive llm eval`, driven through the typer app.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/commands.py (codingrules section 3). The evaluation itself
    is replaced by a stand-in returning a canned report, so these tests cover the command's own
    job: parsing options, choosing goals, opening the ledger only for `--update-map`, writing the
    report where `--report-dir` says, printing, and turning bad input into exit 2.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.commands for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.cli import fake_manifest
from builders.evals import make_eval_report, make_goal_result
from typer.testing import CliRunner, Result

import hivemind.cli.llm.eval.commands as commands_module
from hivemind.cli.app import app
from hivemind.cli.llm.eval.pipeline import EvalRequest
from hivemind.cli.llm.eval.report import EvalReport
from hivemind.cli.llm.eval.transcriber import FixtureSetError
from hivemind.queen.forage.ledger import InMemoryLedgerStore

runner = CliRunner()


class _Spy:
    """Stand-in for `run_evaluation`: records the request and returns a canned report."""

    def __init__(self, report: EvalReport | None = None) -> None:
        self.requests: list[EvalRequest] = []
        self._report = report if report is not None else make_eval_report()

    def __call__(self, request: EvalRequest) -> EvalReport:
        self.requests.append(request)
        if request.on_goal is not None:
            request.on_goal(make_goal_result())
        return self._report


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> _Spy:
    spy = _Spy()
    monkeypatch.setattr(commands_module, "run_evaluation", spy)
    return spy


def _eval(tmp_path: Path, *args: str) -> Result:
    manifest = fake_manifest(tmp_path)
    return runner.invoke(
        app,
        ["llm", "eval", "--manifest", str(manifest), "--report-dir", str(tmp_path / "out"), *args],
    )


def test_eval_is_listed_under_hive_llm() -> None:
    result = runner.invoke(app, ["llm", "--help"])

    assert result.exit_code == 0
    assert "eval" in result.output


def test_eval_runs_every_goal_by_default_and_writes_both_report_files(
    tmp_path: Path, spy: _Spy
) -> None:
    result = _eval(tmp_path)

    assert result.exit_code == 0, result.output
    assert [goal.name for goal in spy.requests[0].goals] == [
        "haiku",
        "command",
        "ask",
        "checkpoint",
    ]
    assert sorted(path.name for path in (tmp_path / "out").iterdir()) == [
        "2026-10-11.json",
        "2026-10-11.md",
    ]


def test_eval_prints_a_line_per_goal_the_pass_count_and_where_the_report_went(
    tmp_path: Path, spy: _Spy
) -> None:
    output = _eval(tmp_path).output

    assert "haiku" in output and "PASS" in output
    assert "goals passed: 1/1" in output
    assert "measured fake_default: grade 5 (not written)" in output
    assert "2026-10-11.md" in output


def test_eval_goals_option_selects_the_named_goals_only(tmp_path: Path, spy: _Spy) -> None:
    _eval(tmp_path, "--goals", "haiku, ask")

    assert [goal.name for goal in spy.requests[0].goals] == ["haiku", "ask"]


def test_eval_with_an_unknown_goal_exits_two_and_runs_nothing(tmp_path: Path, spy: _Spy) -> None:
    result = _eval(tmp_path, "--goals", "nope")

    assert result.exit_code == 2
    assert "unknown goal(s) nope" in result.output
    assert spy.requests == []


def test_eval_opens_the_ledger_only_for_update_map(
    tmp_path: Path, spy: _Spy, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = InMemoryLedgerStore()
    monkeypatch.setattr(commands_module, "open_ledger", lambda _db: store)

    _eval(tmp_path)
    _eval(tmp_path, "--update-map")

    assert [request.ledger for request in spy.requests] == [None, store]


def test_eval_says_measured_grades_were_written_when_the_report_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands_module, "run_evaluation", _Spy(make_eval_report(map_updated=True)))
    monkeypatch.setattr(commands_module, "open_ledger", lambda _db: InMemoryLedgerStore())

    result = _eval(tmp_path, "--update-map")

    assert "grade 5 (written)" in result.output


def test_eval_passes_the_fixture_directory_through(tmp_path: Path, spy: _Spy) -> None:
    _eval(tmp_path, "--fixtures", str(tmp_path / "clips"))

    assert spy.requests[0].fixtures == tmp_path / "clips"


def test_eval_with_a_bad_fixture_directory_exits_two_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_request: EvalRequest) -> EvalReport:
        raise FixtureSetError("no <clip>.wav files in clips")

    monkeypatch.setattr(commands_module, "run_evaluation", refuse)

    result = _eval(tmp_path, "--fixtures", "clips")

    assert result.exit_code == 2
    assert "no <clip>.wav files" in result.output


def test_eval_with_a_missing_manifest_exits_two(tmp_path: Path, spy: _Spy) -> None:
    result = runner.invoke(app, ["llm", "eval", "--manifest", str(tmp_path / "absent.toml")])

    assert result.exit_code == 2
    assert spy.requests == []
