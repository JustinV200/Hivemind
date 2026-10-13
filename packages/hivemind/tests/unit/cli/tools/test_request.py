"""Tests for hivemind.cli.tools.request: `hive tools request` and the submitter seam.

Fits into the Hive:
    Mirrors src/hivemind/cli/tools/request.py (codingrules section 3). Drives the real typer app
    against a real fake-provider manifest. The seam is exercised by swapping `in_process_submitter`
    for a recording submitter; the real submitter, which builds and runs a Hive, is tested in
    test_submit.py.

Key invariants:
    - None: this module holds tests only.

See Also:
    - hivemind.cli.tools.request for the module under test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from builders.royal_jelly import make_tool_request
from unit.cli.tools.harness import invoke, make_hive

import hivemind.cli.tools.request as request_module
from hivemind.cell import hive_stand_cell_id
from hivemind.guard import CapabilitySet
from hivemind.manifest import load_manifest
from waggle.clock import FakeClock
from waggle.ids import new_cell_id
from waggle.messages.labels import OsFamily
from waggle.messages.tool.authoring import QuarantinePath, ToolPromoted, ToolRequest, ToolScope


class _Recorder:
    """A submitter that remembers each request and answers with a fixed outcome."""

    def __init__(self, promote: bool) -> None:
        self.requests: list[ToolRequest] = []
        self._promote = promote

    async def submit(self, request: ToolRequest) -> ToolPromoted | None:
        self.requests.append(request)
        if not self._promote:
            return None
        return ToolPromoted(
            tool_id=request.tool_id,
            name=request.name,
            version="1.0.0",
            scope=request.scope,
            cell_id=request.cell_id if request.scope is ToolScope.CELL else None,
            description=request.description,
            input_schema_json='{"type": "object", "additionalProperties": false}',
            required_capabilities=request.required_capabilities,
            supported_os=(request.platform.os,),
            quarantine_path=QuarantinePath.SANDBOX_CELL,
            quarantined_on=request.cell_id,
            quarantine_passed_at=FakeClock().now(),
            package_sha256="ab" * 32,
            package_size_bytes=100,
            promoted_by_warden=request.warden_id if request.scope is ToolScope.CELL else None,
            supersedes_version=None,
            reason="Promoted for a test.",
        )


def _swap_submitter(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    """Replace the default stub with `recorder`: the one function swap the seam promises."""
    monkeypatch.setattr(request_module, "in_process_submitter", lambda _manifest: recorder)


def test_a_valid_request_reaches_the_submitter_for_the_hive_stand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = make_hive(tmp_path)
    recorder = _Recorder(promote=True)
    _swap_submitter(monkeypatch, recorder)

    result = invoke(
        manifest_path,
        *("request", "echo_tool", "--description", "Echo it.", "--capability", "net:example.org"),
        *("--capability", "fs:read:**"),
    )

    assert result.exit_code == 0, result.output
    (request,) = recorder.requests
    node_id = load_manifest(manifest_path, {}).hive.node_id
    assert request.cell_id == hive_stand_cell_id(node_id) and request.scope is ToolScope.HIVE
    assert request.required_capabilities == ("net:example.org", "fs:read:**")
    assert request.worker_id is None and request.task_id is None
    assert request.platform.os in set(OsFamily)  # Probed from this machine, not invented.
    assert "promoted echo_tool 1.0.0 (HIVE); package abababababab" in result.stdout


def test_cell_names_a_cell_scope_request_for_that_cell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(promote=True)
    _swap_submitter(monkeypatch, recorder)
    cell_id = new_cell_id(FakeClock())

    result = invoke(
        make_hive(tmp_path),
        *("request", "echo_tool", "--description", "Echo it.", "--cell", str(cell_id)),
    )

    assert result.exit_code == 0, result.output
    (request,) = recorder.requests
    assert request.scope is ToolScope.CELL and request.cell_id == cell_id


def test_a_submitter_that_promotes_nothing_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _swap_submitter(monkeypatch, _Recorder(promote=False))

    result = invoke(make_hive(tmp_path), "request", "echo_tool", "--description", "Echo it.")

    assert result.exit_code == 1
    assert "echo_tool was not promoted" in result.stderr


def test_a_tool_capability_is_refused_and_never_submitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(promote=True)
    _swap_submitter(monkeypatch, recorder)

    result = invoke(
        make_hive(tmp_path),
        *("request", "echo_tool", "--description", "Echo it.", "--capability", "tool:other"),
    )

    assert result.exit_code == 1
    assert "refused: required capability 'tool:other' is a tool capability" in result.stderr
    assert recorder.requests == []


def test_every_reason_is_printed_when_a_request_breaks_several_rules(tmp_path: Path) -> None:
    result = invoke(
        make_hive(tmp_path),
        *("request", "echo_tool", "--description", "Echo it.", "--capability", "bogus"),
        *("--capability", "tool:other"),
    )

    assert result.exit_code == 1
    assert "'bogus' does not parse" in result.stderr and "'tool:other'" in result.stderr


def test_a_name_the_wire_would_refuse_is_bad_input(tmp_path: Path) -> None:
    result = invoke(make_hive(tmp_path), "request", "Not-A-Name", "--description", "Echo it.")

    assert result.exit_code == 2
    assert "name" in result.stderr


def test_check_request_refuses_a_capability_outside_the_operators_own_set() -> None:
    request = make_tool_request(required_capabilities=("net:example.org", "fs:read:**"))
    narrow = CapabilitySet.parse("net:example.org")

    validation = request_module.check_request(request, narrow)

    assert not validation.ok
    assert validation.reasons == (
        "required capability 'fs:read:**' is outside the requester's own set",
    )
