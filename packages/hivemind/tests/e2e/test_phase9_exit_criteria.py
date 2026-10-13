"""Prove phase 9's first three exit criteria: a Forager's tool, a Cell's own tool, a hostile one.

Roadmap phase 9's exit criteria, each driven through a whole Hive built with `build_hive`, a real
Queen, Warden and Worker, the real Quarantine Comb and a scripted `FakeLLMProvider` answering the
planner, the Worker and the SCAFFOLDER. Criterion 1: a Forager that needs a capability no tool has
asks for it, the Queen's flow scaffolds, proves, promotes and announces it, and the same Forager
calls it and finishes with its output (the Hive Stand's Comb runs in the development subprocess
sandbox: no Docker exists here). Criterion 2: the Hive Stand's Warden promotes a cell-scope tool for
its own Cell, proved in a sandbox Virtual Cell whose own in-process Warden runs the Comb over its
session, and a Worker on another Cell is never offered it. Criterion 3: three malicious scaffolds
fail the Comb with the construct named, are rejected after the one retry and leave no registry row.
Criterion 4 (the `local_llm` job) is `tests/evals/scaffold`; criterion 5 is
`test_phase9_exit_criteria_basket.py`.

Fits into the Hive:
    Test infrastructure (codingrules section 14), not shipped. Exercises `hivemind.queen.ticks.
    tools`, `hivemind.wardens.ticks.tools`, `hivemind.royal_jelly` and
    `hivemind.workers.tools.comb`.

Key invariants:
    - Everything but the models is real; a Hive is built outside any event loop, then run in one.
    - A scenario reads the trail and the registry inside the running Hive (`run_lab`), then asserts.

See Also:
    - tests.e2e.royal_jelly_runs for the scripts, the manifest and `run_lab`.
    - tests.e2e.royal_jelly_forager for the Forager's fake browser.
    - .claude/roadmap.md lines 1497-1508 for the criteria.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.virtual_cells import VirtualCellsTuning, virtual_cells_manifest
from e2e.kernel_helpers import assert_kinds_in_order, single_task_plan
from e2e.royal_jelly_forager import forager_plan, forager_turn, pin_fake_browser
from e2e.royal_jelly_runs import (
    ANSWER_FILE,
    NOTES_LINES,
    NOTES_NAME,
    TOOL_NAME,
    LabScript,
    ProbeTurns,
    append_lab,
    build_lab_hive,
    count_lines_reply,
    install_cell_script,
    lab_manifest,
    pin_host_platform,
    probe_plan,
    request_then_use_turn,
    run_lab,
)

from hivemind.cell import hive_stand_cell_id
from hivemind.cli.compose import GoalReport, Hive
from hivemind.pheromone import TrailQuery
from waggle.ids import CellId
from waggle.messages.tool import ToolScope

pytestmark = pytest.mark.e2e

_PROMOTION_ORDER = [
    "tool.requested",
    "tool.scaffolded",
    "tool.quarantine_requested",
    "tool.quarantined",
    "tool.promoted",
    "tool.announced",
]
_SOCKET_TOOL = """\
import socket


async def run(session, arguments):
    return {"host": socket.gethostname()}
"""
_SUBPROCESS_TOOL = """\
import subprocess


async def run(session, arguments):
    return {"code": subprocess.run(["true"], check=False).returncode}
"""
_OPEN_TOOL = """\
async def run(session, arguments):
    with open("/etc/hostname") as handle:
        return {"host": handle.read()}
"""
_SANDBOX_RELEASED = ["tool.sandbox_provisioned", "tool.sandbox_released"]


def test_a_forager_needing_a_missing_tool_gets_it_scaffolded_proved_promoted_and_uses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 1, with a Forager over the in-memory login site on the Hive Stand."""
    # Arrange: a Forager that logs in, keeps a file, asks for a line counter, then calls it.
    pin_host_platform(monkeypatch)
    pin_fake_browser(monkeypatch)
    script = LabScript(forager_turn, forager_plan(), [count_lines_reply()])
    hive = build_lab_hive(lab_manifest(tmp_path, role="forager"), script)

    # Act
    run = asyncio.run(run_lab(hive, ["Log in and count the lines of what you keep."]))

    # Assert: the whole story is on the trail, in order, and the Forager finished with the output.
    (report,) = run.reports
    assert report.succeeded
    assert_kinds_in_order(run.tool_kinds, _PROMOTION_ORDER)
    assert run.of_kind("tool.quarantined")[0].payload["passed"] is True
    assert_kinds_in_order(run.tool_kinds, ["tool.promoted", "tool.loaded"])
    (row,) = run.rows
    assert (row.name, row.scope, row.promoted_by) == (TOOL_NAME, ToolScope.HIVE, "queen")
    assert row.package_sha256.startswith(
        str(run.of_kind("tool.loaded")[0].payload["package_sha256"])
    )
    assert report.tasks[0].outcome is not None
    assert report.tasks[0].outcome.summary == f"{NOTES_NAME} has {NOTES_LINES} lines."


@dataclass(frozen=True, slots=True)
class _Seen:
    """What the registry shows two Cells' bees: the Hive Stand's, and the later Worker's."""

    other: CellId  # The Virtual Cell the probe task ran on.
    stand_tools: list[str]  # Names `visible_tools(stand)` lists.
    other_tools: list[str]  # Names `visible_tools(other)` lists.


async def _read_registry(hive: Hive, reports: tuple[GoalReport, ...]) -> _Seen:
    """Ask the registry what each Cell's bees may see, once both goals ended."""
    assigned = await hive.stores.trail.query(TrailQuery(kind="queen.assigned"))
    probed = str(reports[1].tasks[0].id)
    other = next(CellId(str(e.payload["cell_id"])) for e in assigned if e.subject_id == probed)
    access = hive.royal_jelly
    assert access is not None
    stand = hive.warden_link.cell.id
    return _Seen(
        other=other,
        stand_tools=[row.name for row in await access.registry.visible_tools(stand)],
        other_tools=[row.name for row in await access.registry.visible_tools(other)],
    )


def _cell_scope_hive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe: ProbeTurns) -> Hive:
    """A Hive preferring the Hive Stand, whose sandbox is a real in-process in-Cell Warden."""
    pin_host_platform(monkeypatch)
    manifest = virtual_cells_manifest(
        tmp_path, tuning=VirtualCellsTuning(prefer="real", overwinter_enabled=False)
    )
    append_lab(manifest, role="drone", royal_jelly='sandbox = "virtual"\nrequest_timeout_s = 120\n')
    plans = [single_task_plan(ANSWER_FILE), probe_plan()]
    script = LabScript(probe.turn, plans, [count_lines_reply()])
    install_cell_script(monkeypatch, script)
    return build_lab_hive(manifest, script)


def test_a_warden_promotes_a_cell_scope_tool_that_only_its_own_cell_can_see(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Criterion 2: the Hive Stand's Warden scaffolds, gets a sandbox, promotes at CELL scope."""
    # Arrange: a second goal that must run on a Virtual Cell of its own.
    probe = ProbeTurns("cell")
    hive = _cell_scope_hive(tmp_path, monkeypatch, probe)
    stand = hive_stand_cell_id(hive.manifest.hive.node_id)

    # Act
    goals = ["Count the lines.", "Probe another Cell."]
    run = asyncio.run(run_lab(hive, goals, after=_read_registry))

    # Assert: promoted at CELL scope by this Warden, after a sandbox Cell proved it.
    assert all(report.succeeded for report in run.reports)
    order = ["tool.requested", "tool.scaffolded", "tool.quarantine_requested", *_SANDBOX_RELEASED]
    assert_kinds_in_order(run.tool_kinds, [*order, "tool.quarantined", "tool.promoted"])
    (row,) = run.rows
    warden = str(hive.warden_link.warden_id)
    assert (row.scope, row.cell_id, row.promoted_by) == (ToolScope.CELL, stand, warden)
    sandbox = run.of_kind("tool.sandbox_provisioned")[0].payload["sandbox_cell_id"]
    assert run.of_kind("tool.quarantined")[0].payload["sandbox_cell_id"] == sandbox != stand
    # Only that Cell's bees see it: the later Worker's Cell is another and was not offered it.
    seen: _Seen = run.extra
    assert seen.other != stand
    assert probe.offered and all(TOOL_NAME not in names for names in probe.offered)
    assert (seen.stand_tools, seen.other_tools) == ([TOOL_NAME], [])


@pytest.mark.parametrize(
    ("tool_source", "construct"),
    [(_SOCKET_TOOL, "socket"), (_SUBPROCESS_TOOL, "subprocess"), (_OPEN_TOOL, "open")],
    ids=["socket", "subprocess", "reads_outside_its_dir"],
)
def test_a_malicious_scaffold_fails_the_comb_naming_the_construct_and_is_never_promoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool_source: str, construct: str
) -> None:
    """Criterion 3: socket, `subprocess` and a read outside the tool's dir each fail, twice."""
    # Arrange: the scaffolder writes the same hostile tool on the attempt and on the one retry.
    pin_host_platform(monkeypatch)
    hostile = count_lines_reply(tool_source=tool_source, capabilities=())
    script = LabScript(request_then_use_turn, single_task_plan(ANSWER_FILE), [hostile])
    hive = build_lab_hive(lab_manifest(tmp_path), script)

    # Act
    run = asyncio.run(run_lab(hive, ["Count the lines."]))

    # Assert: two failing reports naming the gate and the construct, the rejection, no row.
    failures = [event.payload for event in run.of_kind("tool.quarantined")]
    assert [f["passed"] for f in failures] == [False, False]
    assert {f["failing_gate"] for f in failures} == {"static"}
    assert all(construct in str(f["reason"]) for f in failures)
    assert run.tool_kinds[-1] == "tool.rejected" and "tool.promoted" not in run.tool_kinds
    assert run.rows == ()
    # The retry was shown the failing report, and the requester was told the tool was rejected.
    assert len(script.scaffold_requests) == 2
    assert "Failing gate: static" in (script.scaffold_requests[1].system or "")
    (report,) = run.reports
    assert report.tasks[0].outcome is not None
    assert "hive.tool.rejected" in report.tasks[0].outcome.summary
