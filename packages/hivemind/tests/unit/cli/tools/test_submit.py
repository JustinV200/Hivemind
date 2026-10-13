"""Tests for hivemind.cli.tools.submit: `hive tools request` against a Hive it builds and runs.

Fits into the Hive:
    Mirrors src/hivemind/cli/tools/submit.py (codingrules section 3). The command is driven
    through the real typer app over a `fake_manifest` Hive with the development subprocess sandbox,
    its SCAFFOLDER scripted through the fake provider's responders: the first test runs the real
    Quarantine Comb and finds the promoted tool in the Hive's database afterwards; the rest end at
    the Comb's static gate or before it, so they cost no process.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.tools.submit for the module under test.
    - tests/e2e/test_phase9_exit_criteria.py for the same flow driven by a Worker's request.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest
from unit.cli.tools.harness import invoke, make_hive, rows, trail_events

import hivemind.cli.tools.request as request_module
from hivemind.forage.slots import ModelSlot
from hivemind.llm import LLMRequest, LLMResponse, text_response
from hivemind.manifest import load_manifest
from hivemind.royal_jelly.scaffold import ScaffoldReply
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.tool.authoring import ToolScope

_TOOL = """\
from pathlib import Path


async def run(session, arguments):
    data = await session.get_file(Path("data") / arguments["name"])
    return {"lines": len(data.splitlines())}
"""
_TEST = """\
import asyncio
import unittest
from pathlib import Path

from tool import run


class CountLinesTest(unittest.TestCase):
    def test_counts_the_lines_of_a_scratch_file(self):
        folder = Path(SESSION.scratch_dir) / "data"
        folder.mkdir()
        (folder / "notes.txt").write_text("a\\nb\\nc\\n")
        self.assertEqual(asyncio.run(run(SESSION, {"name": "notes.txt"})), {"lines": 3})
"""
_SOCKET_TOOL = "import socket\n\n\nasync def run(session, arguments):\n    return {}\n"
_SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}
_CAPABILITIES = ("fs:read:data", "fs:read:data/*")
_ASK = (
    *("request", "count_lines", "--description", "Count the lines of a file in the data folder."),
    *("--capability", "fs:read:data", "--capability", "fs:read:data/*"),
)


def _scaffolds(tool_source: str) -> functools.partial[LLMResponse]:
    """A responder answering the SCAFFOLDER slot with a tool whose `tool.py` is `tool_source`."""
    reply = ScaffoldReply(
        description="Count the lines of a file in the data folder and return the count.",
        input_schema_json=json.dumps(_SCHEMA),
        test_source=_TEST,
        tool_source=tool_source,
        required_capabilities=_CAPABILITIES,
    )

    def respond(body: str, request: LLMRequest) -> LLMResponse:
        # Every other slot gets an empty object; only the scaffolder is ever asked in this Hive.
        if request.slot is not ModelSlot.SCAFFOLDER:
            return text_response("{}")
        fenced = request.response_schema is None
        return text_response(f"```json\n{body}\n```" if fenced else body)

    return functools.partial(respond, reply.model_dump_json())


def _hive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_source: str) -> Path:
    """A Hive manifest with the subprocess sandbox, and the command's submitter scripted."""
    manifest_path = make_hive(tmp_path)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write('\n[royal_jelly]\nsandbox = "subprocess"\nrequest_timeout_s = 60\n')
    real = request_module.in_process_submitter
    scripted = functools.partial(real, environ={}, responders={"fake": _scaffolds(tool_source)})
    monkeypatch.setattr(request_module, "in_process_submitter", scripted)
    return manifest_path


def test_a_request_is_scaffolded_proved_and_promoted_in_the_hive_it_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _hive(tmp_path, monkeypatch, _TOOL)

    result = invoke(manifest_path, *_ASK)

    assert result.exit_code == 0, result.output + result.stderr
    assert "promoted count_lines 1.0.0 (HIVE); package " in result.stdout
    (row,) = rows(manifest_path)
    assert (row.name, row.scope, row.promoted_by) == ("count_lines", ToolScope.HIVE, "queen")
    kinds = [
        event.kind
        for kind in ("tool.promoted", "tool.announced")
        for event in trail_events(manifest_path, kind)
    ]
    assert kinds == ["tool.promoted", "tool.announced"]


def test_a_tool_the_comb_rejects_exits_1_with_the_gate_and_promotes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _hive(tmp_path, monkeypatch, _SOCKET_TOOL)

    result = invoke(manifest_path, *_ASK)

    assert result.exit_code == 1
    assert "hive.tool.rejected" in result.stderr and "static" in result.stderr
    assert "count_lines was not promoted" in result.stderr
    assert rows(manifest_path) == ()
    assert [e.payload["passed"] for e in trail_events(manifest_path, "tool.quarantined")] == [
        False,
        False,
    ]


def test_a_cell_scope_request_is_not_submitted_because_only_a_cells_own_warden_builds_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _hive(tmp_path, monkeypatch, _TOOL)
    cell = str(new_cell_id(FakeClock()))

    result = invoke(manifest_path, *_ASK, "--cell", cell)

    assert result.exit_code == 2
    assert "a cell-scope tool is built by the Warden of its own Cell" in result.stderr
    assert rows(manifest_path) == ()


def test_the_default_submitter_is_the_hive_submitter_for_the_manifest(tmp_path: Path) -> None:
    manifest = load_manifest(make_hive(tmp_path), {})

    submitter = request_module.in_process_submitter(manifest, environ={})

    assert type(submitter).__name__ == "HiveSubmitter"
