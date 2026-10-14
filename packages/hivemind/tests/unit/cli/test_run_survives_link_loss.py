"""Tests for `hive run --survives-link-loss`: the operator's promise reaches every planned task.

The flag says the goal's work must keep going if its device loses the link to the Hive Stand, which
is what the Queen's rung decisions read to give a device a Warden and a model server of its own.
Given on a plain goal it travels with the goal's terms; given with a tier the goal is asked for as
a request and the promise rides on the request; a remote goal cannot carry it, so the command
refuses rather than drop it.

Fits into the Hive:
    Mirrors src/hivemind/cli/run.py (codingrules section 5.1: split by feature from test_run.py).
    Drives the typer application through `typer.testing.CliRunner` over a `builders.cli.
    fake_manifest` Hive whose `fake` provider is scripted, as test_run_comb_shield.py does.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.run for the command under test.
    - hivemind.queen.planner.plan for where the promise reaches the tasks' needs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from typer.testing import CliRunner
from unit.cli.test_run_comb_shield import _patch_build_hive, _plan, _requests

from hivemind.brood_chamber import Task, TaskFilter
from hivemind.cli.app import app
from hivemind.cli.compose import Hive

runner = CliRunner()


def _tasks(hive: Hive) -> list[Task]:
    """Every task the run planned, read back from the Hive's own Brood Chamber."""
    return list(asyncio.run(hive.stores.chamber.list(TaskFilter())))


def _args(tmp_path: Path, *extra: str) -> list[str]:
    """The `hive run` arguments for one goal, with `extra` appended."""
    manifest = str(fake_manifest(tmp_path))
    return ["run", "write a note", "--manifest", manifest, "--timeout", "30", *extra]


def test_the_flag_makes_every_planned_task_need_to_survive_a_lost_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Hive] = []
    _patch_build_hive(monkeypatch, _plan({}), built)

    result = runner.invoke(app, _args(tmp_path, "--survives-link-loss"))

    assert result.exit_code == 0, result.output
    [task] = _tasks(built[0])
    assert task.spec.needs.survives_link_loss is True


def test_without_the_flag_nothing_is_promised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Hive] = []
    _patch_build_hive(monkeypatch, _plan({}), built)

    result = runner.invoke(app, _args(tmp_path))

    assert result.exit_code == 0, result.output
    [task] = _tasks(built[0])
    assert task.spec.needs.survives_link_loss is False


def test_with_a_tier_the_promise_rides_on_the_goal_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Hive] = []
    _patch_build_hive(monkeypatch, _plan({}), built)

    result = runner.invoke(app, _args(tmp_path, "--comb-shield", "meadow", "--survives-link-loss"))

    assert result.exit_code == 0, result.output
    [request] = _requests(built[0])
    assert request.survives_link_loss is True
    [task] = _tasks(built[0])
    assert task.spec.needs.survives_link_loss is True


def test_a_remote_goal_cannot_carry_the_promise_so_the_command_refuses(tmp_path: Path) -> None:
    result = runner.invoke(app, _args(tmp_path, "--survives-link-loss", "--remote"))

    assert result.exit_code == 2
    assert "cannot be given with --remote" in result.output
