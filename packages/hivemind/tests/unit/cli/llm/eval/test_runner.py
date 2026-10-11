"""Tests for hivemind.cli.llm.eval.runner: one goal on a real Hive over a scripted fake provider.

Fits into the Hive:
    Mirrors src/hivemind/cli/llm/eval/runner.py (codingrules section 3). Each run builds a real
    Hive on a real SQLite file in a private directory and runs it to the end, so the evidence the
    graders read is the evidence a live run produces; only the model is scripted
    (`builders.evals.scripted_responder`).

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.llm.eval.runner for the module under test.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from builders.cli import fake_manifest
from builders.evals import scripted_responder
from builders.trail import make_trail

import hivemind.cli.llm.eval.runner as runner_module
from hivemind.cli.compose import Hive
from hivemind.cli.llm.eval.goals import GOAL_SET, EvalGoal
from hivemind.cli.llm.eval.runner import RunnerOptions, run_goal_evidence
from hivemind.manifest import HiveManifest, load_manifest
from waggle.clock import SystemClock

_BY_NAME = {goal.name: goal for goal in GOAL_SET}


def _manifest(tmp_path: Path) -> HiveManifest:
    return load_manifest(fake_manifest(tmp_path), {})


def _goal(name: str) -> EvalGoal:
    """Return one of the fixed goals by name."""
    return _BY_NAME[name]


def _options(tmp_path: Path, goal: str, **overrides: object) -> RunnerOptions:
    fields: dict[str, object] = {
        "environ": {},
        "clock": SystemClock(),
        "work_dir": tmp_path / "work",
        "responders": {"fake": scripted_responder(goal)},
    }
    fields.update(overrides)
    return RunnerOptions(**fields)  # type: ignore[arg-type]


def test_a_haiku_run_returns_the_tasks_events_and_scratch_files(tmp_path: Path) -> None:
    evidence = run_goal_evidence(_manifest(tmp_path), _goal("haiku"), _options(tmp_path, "haiku"))

    assert evidence.error is None
    assert evidence.succeeded and not evidence.timed_out
    assert len(evidence.tasks) == 1
    assert set(evidence.files) == {"haiku_1.txt", "haiku_2.txt", "haiku_3.txt"}
    kinds = {event.kind for event in evidence.events}
    assert {"queen.planned", "llm.call", "task.succeeded"} <= kinds
    assert evidence.start_rung == "NATIVE"  # The fake provider declares every capability.


def test_a_run_never_touches_the_operators_own_hive_state(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    options = _options(tmp_path, "haiku")

    run_goal_evidence(manifest, _goal("haiku"), options)

    assert not manifest.resolve_path(manifest.hive.db).exists()
    assert (options.work_dir / "haiku" / "hive.db").exists()


def test_each_goal_gets_its_own_fresh_trail(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    first = run_goal_evidence(manifest, _goal("haiku"), _options(tmp_path, "haiku"))

    second = run_goal_evidence(manifest, _goal("command"), _options(tmp_path, "command"))

    # The second run's trail holds only its own single plan, not the first goal's.
    assert sum(1 for event in second.events if event.kind == "queen.planned") == 1
    assert sum(1 for event in first.events if event.kind == "queen.planned") == 1


def test_the_command_goal_leaves_what_the_command_printed(tmp_path: Path) -> None:
    evidence = run_goal_evidence(
        _manifest(tmp_path), _goal("command"), _options(tmp_path, "command")
    )

    assert evidence.succeeded
    assert evidence.files["answer.txt"].strip() == "42"


def test_the_attendant_answers_the_question_the_ask_goal_provokes(tmp_path: Path) -> None:
    evidence = run_goal_evidence(_manifest(tmp_path), _goal("ask"), _options(tmp_path, "ask"))

    kinds = [event.kind for event in evidence.events]
    assert evidence.succeeded, evidence.error
    assert "task.blocked" in kinds
    assert "task.answered" in kinds
    assert "season.txt" in evidence.files


def test_a_checkpoint_the_script_triggers_is_resumed_and_the_goal_finishes(
    tmp_path: Path,
) -> None:
    hive_box: list[Hive] = []
    options = _options(
        tmp_path,
        "checkpoint",
        responders={"fake": scripted_responder("checkpoint", hive_box)},
        on_hive=lambda _goal, hive: hive_box.append(hive),
        force_checkpoints=False,  # The script above sends its own.
    )

    evidence = run_goal_evidence(_manifest(tmp_path), _goal("checkpoint"), options)

    kinds = [event.kind for event in evidence.events]
    assert evidence.succeeded, evidence.error
    assert "memory.checkpoint" in kinds
    assert "worker.resumed" in kinds
    assert len(evidence.files) == 4


def test_a_goal_that_cannot_run_comes_back_as_an_error_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> Hive:
        raise RuntimeError("provider refused to start")

    monkeypatch.setattr(runner_module, "build_hive", refuse)

    evidence = run_goal_evidence(_manifest(tmp_path), _goal("haiku"), _options(tmp_path, "haiku"))

    assert evidence.error == "RuntimeError: provider refused to start"
    assert not evidence.succeeded
    assert evidence.events == ()


# ──────────────────────────────────────────────────────────────────────────────
# The attendant's two levers, against stand-ins for the parts of a Hive they touch
# ──────────────────────────────────────────────────────────────────────────────


class _FakeTrail:
    """A trail that answers every query with the same events."""

    def __init__(self, events: tuple[object, ...]) -> None:
        self._events = events

    async def query(self, _query: object) -> tuple[object, ...]:
        return self._events


class _FakeWarden:
    """A Warden with some sub-bees that records every intervention sent."""

    def __init__(self, worker_ids: tuple[str, ...]) -> None:
        self.sub_bees = tuple(SimpleNamespace(worker_id=worker_id) for worker_id in worker_ids)
        self.sent: list[tuple[str, object]] = []

    async def intervene(self, worker_id: str, intervention: object) -> None:
        self.sent.append((worker_id, intervention))


def _hive_with(warden: _FakeWarden, events: tuple[object, ...]) -> Hive:
    return SimpleNamespace(warden=warden, stores=SimpleNamespace(trail=_FakeTrail(events)))  # type: ignore[return-value]


async def test_a_checkpoint_waits_until_the_worker_has_landed_an_action() -> None:
    warden = _FakeWarden(("worker_1",))

    sent = await runner_module._checkpoint_mid_task(_hive_with(warden, ()))

    assert not sent
    assert warden.sent == []


async def test_a_checkpoint_waits_until_there_is_a_sub_bee_to_checkpoint() -> None:
    warden = _FakeWarden(())

    sent = await runner_module._checkpoint_mid_task(
        _hive_with(warden, make_trail(["capping.applied"]))
    )

    assert not sent


async def test_a_checkpoint_is_sent_to_the_first_sub_bee_once_an_action_landed() -> None:
    warden = _FakeWarden(("worker_1", "worker_2"))

    sent = await runner_module._checkpoint_mid_task(
        _hive_with(warden, make_trail(["capping.applied"]))
    )

    assert sent
    assert [worker for worker, _ in warden.sent] == ["worker_1"]
