"""Tests for `hive run --comb-shield`: a named tier is asked for as a goal request (roadmap 10.3c).

The operator names a tier on the command line, and the goal is recorded exactly the way the Hive
Entrance records a device's request (origin HUMAN, the tier, no device), planned by the Queen on
her own tick and then followed like any other goal. A request the Queen refuses prints as a
refusal, never as a failure of the Hive.

Fits into the Hive:
    Mirrors src/hivemind/cli/run.py and src/hivemind/cli/compose/request.py (codingrules section
    5.1: split by feature from test_run.py). Drives the typer application through
    `typer.testing.CliRunner` over a `builders.cli.fake_manifest` Hive whose `fake` provider is
    scripted, as test_run.py does.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.run for the command under test.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from builders.cli import fake_manifest
from typer.testing import CliRunner

import hivemind.cli.run as run_module
from hivemind.cell import CombShieldLevel, RequestOrigin
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
from hivemind.queen.intake import GoalRequest, GoalRequestQuery, GoalRequestState
from waggle.clock import Clock

runner = CliRunner()


def _plan(needs: Mapping[str, object]) -> dict[str, object]:
    """A one-task plan whose task writes one file, with `needs`."""
    return {
        "tasks": [
            {
                "key": "note",
                "title": "Write a note",
                "objective": "Write a note about bees to note.txt.",
                "acceptance": [
                    {"kind": "FILE_EXISTS", "subject": "note.txt", "argv": [], "expected": None}
                ],
                "needs": dict(needs),
                "clearance": "C1",
                "depends_on": [],
            }
        ]
    }


def _response(*parts: TextPart | ToolCallPart) -> LLMResponse:
    """One scripted model reply made of `parts`."""
    tool_use = any(isinstance(part, ToolCallPart) for part in parts)
    return LLMResponse(
        parts=parts,
        stop_reason=StopReason.TOOL_USE if tool_use else StopReason.END_TURN,
        usage=Usage(input_tokens=0, output_tokens=0),
        model="test-model",
    )


def _responder(plan: Mapping[str, object]) -> Responder:
    """Answer the planner with `plan`; the Drone writes note.txt, then says it is done."""
    rounds = {"worker": 0}

    def responder(request: LLMRequest) -> LLMResponse:
        if request.slot is ModelSlot.QUEEN:
            return _response(TextPart(text=json.dumps(plan)))
        rounds["worker"] += 1
        if rounds["worker"] == 1:
            call = ToolCall(
                id="call_1", name="write_file", arguments={"path": "note.txt", "content": "bees"}
            )
            return _response(ToolCallPart(call=call))
        return _response(TextPart(text="done"))

    return responder


def _patch_build_hive(
    monkeypatch: pytest.MonkeyPatch, plan: Mapping[str, object], built: list[Hive]
) -> None:
    """Script every `fake` provider with `plan`, and keep the Hive built so it can be read back."""

    def patched(
        manifest: HiveManifest,
        *,
        environ: Mapping[str, str],
        clock: Clock,
        stores: HiveStores | None = None,
        responders: Mapping[str, Responder] | None = None,
    ) -> Hive:
        del responders  # Replaced by this test's own scripted responder.
        hive = real_build_hive(
            manifest,
            environ=environ,
            clock=clock,
            stores=stores,
            responders={"fake": _responder(plan)},
        )
        built.append(hive)
        return hive

    monkeypatch.setattr(run_module, "build_hive", patched)


def _requests(hive: Hive) -> list[GoalRequest]:
    """Every goal request the run recorded, read back from the Hive's own table."""
    return list(asyncio.run(hive.stores.goal_requests.list_requests(GoalRequestQuery())))


def _run(tmp_path: Path, tier: str) -> list[str]:
    """The `hive run` arguments for one goal at `tier`."""
    manifest = str(fake_manifest(tmp_path))
    return ["run", "write a note", "--manifest", manifest, "--timeout", "30", "--comb-shield", tier]


def test_a_named_tier_is_asked_for_as_a_human_goal_request_and_then_followed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Hive] = []
    _patch_build_hive(monkeypatch, _plan({}), built)

    result = runner.invoke(app, _run(tmp_path, "meadow"))

    assert result.exit_code == 0, result.output
    [request] = _requests(built[0])
    assert request.state is GoalRequestState.PLANNED
    assert request.origin is RequestOrigin.HUMAN
    assert request.comb_shield is CombShieldLevel.MEADOW
    assert request.device_id is None and request.capabilities is None
    assert "task.succeeded" in result.output


def test_a_night_veil_goal_that_asks_where_its_cell_is_prints_as_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Hive] = []
    _patch_build_hive(monkeypatch, _plan({"network_scopes": ["169.254.169.254"]}), built)

    result = runner.invoke(app, _run(tmp_path, "night_veil"))

    assert result.exit_code == 1, result.output
    assert "hive run refused: A Night Veil goal is location-blind" in result.output
    assert "hive run failed" not in result.output
    [request] = _requests(built[0])
    assert request.state is GoalRequestState.REFUSED


def test_a_night_veil_goal_on_a_hive_with_no_night_veil_link_is_cancelled_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The fake manifest configures no hidden service or Tor proxy: placement refuses once.
    built: list[Hive] = []
    _patch_build_hive(monkeypatch, _plan({}), built)

    result = runner.invoke(app, _run(tmp_path, "night_veil"))

    assert result.exit_code == 1, result.output
    assert "[CANCELLED]" in result.output
    assert "failed" in result.output.splitlines()[-1]


def test_an_unknown_tier_is_a_usage_error(tmp_path: Path) -> None:
    result = runner.invoke(app, _run(tmp_path, "sunshine"))

    assert result.exit_code == 2
    assert "not a Comb Shield tier" in result.output
