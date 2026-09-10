"""Tests for hivemind.cli.run: `hive run "goal" --manifest ...`.

Fits into the Hive:
    Mirrors src/hivemind/cli/run.py (codingrules section 3). Drives the typer application through
    `typer.testing.CliRunner`, the same way an operator's shell would; `hivemind.cli.run.
    build_hive` is monkeypatched to install a scripted responder on the `"fake"` provider a
    `builders.cli.fake_manifest` manifest declares, mirroring `test_llm.py`'s own `build_registry`
    monkeypatch. `hive run`'s own command body is one of the few places a real `SystemClock` and a
    real sleep are expected (codingrules section 11, "SystemClock only at the command edge"), so
    these tests are not FakeClock-driven; the goal itself finishes in well under a second of real
    time regardless.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.run for the module under test.
    - hivemind.cli.compose for build_hive, the seam every test here patches.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from typer.testing import CliRunner

import hivemind.cli.run as run_module
from hivemind.cli.app import app
from hivemind.cli.compose import Hive, HiveStores
from hivemind.cli.compose import build_hive as real_build_hive
from hivemind.forage.slots import ModelSlot
from hivemind.llm import (
    LLMRequest,
    LLMResponse,
    Responder,
    StopReason,
    TextPart,
    ToolCall,
    ToolCallPart,
    Usage,
)
from hivemind.manifest import HiveManifest
from waggle.clock import Clock

runner = CliRunner()

_FAKE_MODEL_ID = "test-model"
_GOAL = "write three haiku about bees to separate files"
_PLAN = {
    "tasks": [
        {
            "key": "haikus",
            "title": "Write three haiku about bees",
            "objective": "Write three haiku about bees to separate files.",
            "acceptance": [
                {"kind": "FILE_EXISTS", "subject": f"haiku_{i}.txt", "argv": [], "expected": None}
                for i in (1, 2, 3)
            ],
            "needs": {},
            "clearance": "C1",
            "depends_on": [],
        }
    ]
}
_FAILING_PLAN = {
    "tasks": [
        {
            "key": "impossible",
            "title": "Never satisfied",
            "objective": "Do nothing useful.",
            "acceptance": [
                {
                    "kind": "FILE_EXISTS",
                    "subject": "never_written.txt",
                    "argv": [],
                    "expected": None,
                }
            ],
            "needs": {},
            "clearance": "C1",
            "depends_on": [],
        }
    ]
}


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(
        parts=(TextPart(text=text),),
        stop_reason=StopReason.END_TURN,
        usage=Usage(input_tokens=0, output_tokens=0),
        model=_FAKE_MODEL_ID,
    )


def _tool_call_response(*calls: ToolCall) -> LLMResponse:
    return LLMResponse(
        parts=tuple(ToolCallPart(call=call) for call in calls),
        stop_reason=StopReason.TOOL_USE,
        usage=Usage(input_tokens=0, output_tokens=0),
        model=_FAKE_MODEL_ID,
    )


def _responder(plan: Mapping[str, object]) -> Responder:
    """Script the planner's own call with `plan`, and the Drone's two-round tool loop."""
    rounds = {"worker": 0}

    def responder(request: LLMRequest) -> LLMResponse:
        if request.slot is ModelSlot.QUEEN:
            return _text_response(json.dumps(plan))
        if request.slot is ModelSlot.WORKER:
            rounds["worker"] += 1
            if rounds["worker"] == 1 and plan is _PLAN:
                calls = tuple(
                    ToolCall(
                        id=f"call_{i}",
                        name="write_file",
                        arguments={"path": f"haiku_{i}.txt", "content": f"bees {i}"},
                    )
                    for i in (1, 2, 3)
                )
                return _tool_call_response(*calls)
            return _text_response("done")
        return _text_response("{}")

    return responder


def _patch_build_hive(monkeypatch: pytest.MonkeyPatch, plan: Mapping[str, object]) -> None:
    """Monkeypatch hivemind.cli.run.build_hive to script every 'fake' provider it constructs."""

    def patched(
        manifest: HiveManifest,
        *,
        environ: Mapping[str, str],
        clock: Clock,
        stores: HiveStores | None = None,
        responders: Mapping[str, Responder] | None = None,
    ) -> Hive:
        return real_build_hive(
            manifest,
            environ=environ,
            clock=clock,
            stores=stores,
            responders={"fake": _responder(plan)},
        )

    monkeypatch.setattr(run_module, "build_hive", patched)


def test_run_command_streams_progress_and_exits_zero_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    _patch_build_hive(monkeypatch, _PLAN)

    result = runner.invoke(app, ["run", _GOAL, "--manifest", str(manifest_path), "--timeout", "30"])

    assert result.exit_code == 0, result.output
    assert "queen.planned" in result.output
    assert "task.succeeded" in result.output
    assert "succeeded" in result.output.splitlines()[-1]


def test_run_command_prints_json_summary_only_with_the_json_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    _patch_build_hive(monkeypatch, _PLAN)

    result = runner.invoke(
        app, ["run", _GOAL, "--manifest", str(manifest_path), "--timeout", "30", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert "queen.planned" not in result.output  # No streamed lines with --json.
    payload = json.loads(result.output)
    assert payload["succeeded"] is True


def test_run_command_exits_1_when_the_goal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = fake_manifest(tmp_path)
    _patch_build_hive(monkeypatch, _FAILING_PLAN)

    result = runner.invoke(
        app, ["run", "an impossible goal", "--manifest", str(manifest_path), "--timeout", "30"]
    )

    assert result.exit_code == 1, result.output


def test_run_command_exits_2_on_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = fake_manifest(tmp_path)

    # A responder that blocks the event loop thread for real (module docstring: `hive run` is one
    # of the few real-SystemClock, real-sleep places): scripting an impossibly small --timeout
    # against a normally-fast fake responder flaked instead, since real SQLite's own thread-pool
    # awaits give the Warden/Queen background tasks enough genuine scheduling turns to finish the
    # whole goal before the very first elapsed-time check, however small --timeout is. A responder
    # that is actually slow makes the elapsed time --timeout compares against real, not a race.
    def slow_responder(request: LLMRequest) -> LLMResponse:
        time.sleep(0.5)
        return _text_response(json.dumps(_PLAN))

    monkeypatch.setattr(
        run_module,
        "build_hive",
        lambda manifest, *, environ, clock, stores=None, responders=None: real_build_hive(
            manifest,
            environ=environ,
            clock=clock,
            stores=stores,
            responders={"fake": slow_responder},
        ),
    )

    result = runner.invoke(
        app, ["run", _GOAL, "--manifest", str(manifest_path), "--timeout", "0.05"]
    )

    assert result.exit_code == 2, result.output


def test_run_command_exits_2_on_a_missing_manifest(tmp_path: Path) -> None:
    result = runner.invoke(app, ["run", _GOAL, "--manifest", str(tmp_path / "missing.toml")])

    assert result.exit_code == 2
