"""Tests for hivemind.cli.swarm.run: `hive swarm run NODE "GOAL"`.

Fits into the Hive:
    Mirrors src/hivemind/cli/swarm/run.py (codingrules section 3). Drives the real typer app
    against a real manifest and registry file (`harness`) and builds a real Hive from it (the real
    Queen, Wardens and swarm listener on a free port), with one seam replaced: the pinned goal
    runner `hivemind.cli.swarm.run.run_goal_on_device`, which a recorder stands in for so a test
    reads the terms it was handed and chooses the report it returns. What the pin itself does is
    tested in `test_pinned`, and against a real packet in `tests/e2e/test_swarm_cli.py`. The
    properties here are the command's: a name it cannot run on is refused before any Hive is
    built, the verdict and exit code follow `hive run`'s, and a failure ends in one stderr line.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.swarm.run for the module under test.
    - tests/unit/cli/test_run.py for `hive run`, whose output and exit codes this follows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.swarm import free_port
from unit.cli.swarm.harness import enrol, invoke, make_hive

from hivemind.cell import HoneyClearance
from hivemind.cli.compose.hive import GoalReport, GoalRun, Hive
from hivemind.cli.run import print_event
from hivemind.cli.swarm import run as run_module
from hivemind.swarm import GatewayUnavailableError, SwarmNode
from hivemind.swarm.node_state import NodeStatus
from waggle.clock import FakeClock
from waggle.ids import new_task_id


class _Runner:
    """Stands in for the pinned goal runner: keeps what it was handed, answers as told."""

    def __init__(self, report: GoalReport | Exception) -> None:
        """Answer every call with `report`, or raise it if it is an exception."""
        self.report = report
        self.calls: list[tuple[SwarmNode, str, GoalRun]] = []

    async def __call__(self, _hive: Hive, node: SwarmNode, goal: str, run: GoalRun) -> GoalReport:
        """Keep the call, then report or raise."""
        self.calls.append((node, goal, run))
        if isinstance(self.report, Exception):
            raise self.report
        return self.report


def _report(*, succeeded: bool, timed_out: bool = False) -> GoalReport:
    """A goal's report with no tasks, ended the way a test asks."""
    return GoalReport(
        goal_id=new_task_id(FakeClock()),
        tasks=(),
        succeeded=succeeded,
        spend_usd=0.0,
        elapsed_s=1.5,
        timed_out=timed_out,
    )


def _runner(monkeypatch: pytest.MonkeyPatch, report: GoalReport | Exception) -> _Runner:
    """Replace the command's pinned goal runner with a recorder answering `report`."""
    runner = _Runner(report)
    monkeypatch.setattr(run_module, "run_goal_on_device", runner)
    return runner


def _hive(tmp_path: Path) -> Path:
    """A manifest with the Swarm on, its listener on a free loopback port."""
    return make_hive(tmp_path, listen_port=free_port())


def test_run_hands_the_named_device_the_goal_and_the_options_and_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    runner = _runner(monkeypatch, _report(succeeded=True))

    result = invoke(
        manifest, "run", "kitchen-pi", "write a report", "--clearance", "C2", "--timeout", "30"
    )

    assert result.exit_code == 0, result.stderr
    assert "succeeded in 1.5s" in result.stdout
    ((device, goal, run),) = runner.calls
    assert (device.device_id, goal) == (node.device_id, "write a report")
    assert (run.clearance, run.timeout_s) == (HoneyClearance.C2, 30.0)
    assert run.on_event is print_event  # The same lines `hive run` streams, as the goal moves.


def test_run_hands_the_device_the_promise_to_survive_a_lost_link_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _hive(tmp_path)
    enrol(manifest, label="kitchen-pi")
    runner = _runner(monkeypatch, _report(succeeded=True))

    invoke(manifest, "run", "kitchen-pi", "write a report", "--survives-link-loss")
    invoke(manifest, "run", "kitchen-pi", "write a report")

    # Off unless the operator asked: the pin alone promises nothing about a lost link.
    assert [run.survives_link_loss for _, _, run in runner.calls] == [True, False]


def test_run_names_the_device_by_its_cell_id_as_well(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _hive(tmp_path)
    node = enrol(manifest, label="kitchen-pi")
    runner = _runner(monkeypatch, _report(succeeded=True))

    result = invoke(manifest, "run", node.cell_id, "write a report")

    assert result.exit_code == 0, result.stderr
    assert runner.calls[0][0].device_id == node.device_id


@pytest.mark.parametrize(
    ("report", "exit_code", "verdict"),
    [
        (_report(succeeded=False), 1, "failed"),
        (_report(succeeded=False, timed_out=True), 2, "timed out"),
    ],
)
def test_run_exits_as_hive_run_does_when_the_goal_failed_or_ran_out_of_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report: GoalReport,
    exit_code: int,
    verdict: str,
) -> None:
    manifest = _hive(tmp_path)
    enrol(manifest, label="kitchen-pi")
    _runner(monkeypatch, report)

    result = invoke(manifest, "run", "kitchen-pi", "write a report")

    assert result.exit_code == exit_code and verdict in result.stdout


def test_run_turns_a_device_that_never_reconnects_into_one_line_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _hive(tmp_path)
    enrol(manifest, label="kitchen-pi")
    gone = GatewayUnavailableError("Device device_x did not connect to this Hive within 1 s.")
    _runner(monkeypatch, gone)

    result = invoke(manifest, "run", "kitchen-pi", "write a report")

    assert result.exit_code == 1 and result.stdout == ""
    # The last line: a log line (the shared-judge advice, say) may come before it on stderr.
    last = result.stderr.splitlines()[-1]
    assert last.startswith("hive swarm run failed: GatewayUnavailableError:")
    assert "did not connect" in result.stderr and "Traceback" not in result.stderr


def test_run_refuses_a_revoked_device_before_building_any_hive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _hive(tmp_path)
    enrol(manifest, label="kitchen-pi", status=NodeStatus.REVOKED, revoked_at=FakeClock().now())
    runner = _runner(monkeypatch, _report(succeeded=True))

    result = invoke(manifest, "run", "kitchen-pi", "write a report")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.node_revoked")
    assert runner.calls == []


def test_run_refuses_a_name_nobody_has(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _hive(tmp_path)
    runner = _runner(monkeypatch, _report(succeeded=True))

    result = invoke(manifest, "run", "nobody", "write a report")

    assert result.exit_code == 1 and result.stderr.startswith("hive.swarm.node_unknown")
    assert runner.calls == []


def test_run_refuses_while_the_manifest_keeps_the_swarm_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = make_hive(tmp_path, enabled=False)
    enrol(manifest, label="kitchen-pi")
    runner = _runner(monkeypatch, _report(succeeded=True))

    result = invoke(manifest, "run", "kitchen-pi", "write a report")

    assert result.exit_code == 1 and "[swarm] enabled = false" in result.stderr
    assert runner.calls == []


def test_run_rejects_a_clearance_that_does_not_exist_as_bad_input(tmp_path: Path) -> None:
    manifest = _hive(tmp_path)
    enrol(manifest, label="kitchen-pi")

    result = invoke(manifest, "run", "kitchen-pi", "write a report", "--clearance", "C9")

    assert result.exit_code == 2


def test_run_offers_no_remote_option_and_its_help_says_why(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from hivemind.cli.app import app

    runner = CliRunner()

    help_text = " ".join(runner.invoke(app, ["swarm", "run", "--help"]).stdout.split())
    refused = runner.invoke(app, ["swarm", "run", "kitchen-pi", "a goal", "--remote"])

    # A goal given to a remote Hive carries the submitting device's ceiling, which names no Cell.
    assert "There is no --remote: a remote goal cannot name a Cell" in help_text
    assert refused.exit_code == 2
