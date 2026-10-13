"""Test QuarantineComb end to end over SandboxSubprocess: a real runner and real tools.

Fits into the Hive:
    Mirrors src/hivemind/royal_jelly/quarantine_comb/comb.py and runner/
    (codingrules section 3). Test infrastructure, not shipped.

Key invariants:
    - None: this module holds tests only.

See Also:
    - src/hivemind/royal_jelly/quarantine_comb/comb.py and runner/ for the module under test.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import uuid
from collections.abc import Awaitable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from builders.royal_jelly import make_package, make_tool_files, make_tool_spec

from hivemind.royal_jelly.quarantine_comb import QuarantineComb, SandboxSubprocess, default_gates
from hivemind.royal_jelly.spec import GATE_ORDER, QuarantineLimits, QuarantineReport, ToolSpec
from waggle.clock import SystemClock

# A tool that asks the operating system for a process by way of a standard-library helper: the
# static scan cannot see it (nothing banned is named), so only the runner's audit hook can. The
# program is this very interpreter, the one executable every host and container has.
_SPAWN_TOOL = """\
import sys
import uuid


async def run(session, arguments):
    return {"out": str(uuid._get_command_stdout(sys.executable, "-c", "pass"))}
"""
# A tool that reads a file outside its package through a standard-library helper. The file is
# one this module writes beside the sandboxes (the system temp directory lies under no allowed
# root), because `linecache` opens nothing for a path that does not exist, and no one system
# file exists on every platform.
_OUTSIDE_FILE = Path(os.path.realpath(tempfile.gettempdir())) / (
    f"hivemind-comb-outside-{uuid.uuid4().hex}.txt"  # Real path: the hook names resolved paths.
)
_OUTSIDE_TOOL = f"""\
import linecache


async def run(session, arguments):
    return {{"lines": len(linecache.getlines({str(_OUTSIDE_FILE)!r}))}}
"""
_SLEEP_TOOL = """\
import asyncio


async def run(session, arguments):
    await asyncio.sleep(30)
    return {}
"""
_BLOCKING_TOOL = """\
import time


async def run(session, arguments):
    time.sleep(30)
    return {}
"""
# `hostname` exists on Linux, macOS and Windows alike; `ls` and `true` do not.
_HOSTNAME_TOOL = """\
from hivemind.cell.session import ExecSpec


async def run(session, arguments):
    kinds = []
    async for event in session.exec(ExecSpec(argv=("hostname",))):
        kinds.append(type(event).__name__)
    return {"events": kinds}
"""
_OS_SYSTEM_TOOL = """\
import os


async def run(session, arguments):
    return {"status": os.system("true")}
"""
_SPAWNING_TEST = """\
import sys
import unittest
import uuid


class ToolTest(unittest.TestCase):
    def test_spawns(self):
        uuid._get_command_stdout(sys.executable, "-c", "pass")
"""
_SESSION_TEST = """\
import asyncio
import unittest

from tool import run


class ToolTest(unittest.TestCase):
    def test_runs_against_the_session(self):
        result = asyncio.run(run(SESSION, {}))
        self.assertIn("events", result)
"""
_FAST = QuarantineLimits(wall_s=20, fuzz_cases=4)
_SHORT = QuarantineLimits(wall_s=2, fuzz_cases=2)


@dataclass(frozen=True)
class _Scenario:
    """One tool to run through the Comb, and how."""

    files_source: str | None = None
    test_source: str | None = None
    caps: tuple[str, ...] = ("fs:read:*",)
    limits: QuarantineLimits = _FAST
    skip_static: bool = False


_SCENARIOS = {
    "echo": _Scenario(),
    "spawn_at_runtime": _Scenario(_SPAWN_TOOL),
    "reads_outside": _Scenario(_OUTSIDE_TOOL),
    "sleeps": _Scenario(_SLEEP_TOOL, limits=_SHORT),
    "blocks": _Scenario(_BLOCKING_TOOL, limits=_SHORT),
    "hostname_undeclared": _Scenario(_HOSTNAME_TOOL, _SESSION_TEST),
    "hostname_declared": _Scenario(
        _HOSTNAME_TOOL, _SESSION_TEST, caps=("fs:read:*", "exec:hostname")
    ),
    "spawning_tests": _Scenario(test_source=_SPAWNING_TEST),
    "hook_without_static": _Scenario(_OS_SYSTEM_TOOL, skip_static=True),
}


async def _run_one(scenario: _Scenario) -> QuarantineReport:
    """Run one scenario through a fresh Comb and a fresh subprocess sandbox."""
    spec: ToolSpec = make_tool_spec(required_capabilities=scenario.caps)
    if scenario.files_source is None and scenario.test_source is None:
        package = make_package(spec=spec)
    else:
        source = scenario.files_source or "async def run(session, arguments):\n    return {}\n"
        package = make_package(make_tool_files(source, scenario.test_source, spec))
    gates = default_gates()[1:] if scenario.skip_static else default_gates()
    comb = QuarantineComb(SystemClock(), gates=gates)
    return await comb.run(package, spec, SandboxSubprocess(SystemClock()), scenario.limits)


async def _run_all() -> dict[str, QuarantineReport]:
    """Run every scenario concurrently: each starts an interpreter, which dominates the time."""
    jobs: dict[str, Awaitable[QuarantineReport]] = {n: _run_one(s) for n, s in _SCENARIOS.items()}
    reports = await asyncio.gather(*jobs.values())
    return dict(zip(jobs, reports, strict=True))


@pytest.fixture(scope="module")
def reports() -> Iterator[dict[str, QuarantineReport]]:
    """Run every tool through the real Comb once for the whole module."""
    _OUTSIDE_FILE.write_text("outside the sandbox\n", encoding="utf-8")
    try:
        yield asyncio.run(_run_all())
    finally:
        _OUTSIDE_FILE.unlink(missing_ok=True)


def test_the_echo_tool_passes_all_six_gates_in_order(reports: dict[str, QuarantineReport]) -> None:
    report = reports["echo"]

    assert report.passed
    assert tuple(gate.name for gate in report.gates) == GATE_ORDER
    assert report.failing_gate is None
    assert report.sandbox_cell_id is None


def test_a_runtime_process_spawn_the_static_scan_cannot_see_fails_the_fuzz_gate(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["spawn_at_runtime"]

    assert report.failing_gate == "fuzz"
    assert [gate.name for gate in report.gates] == ["static", "tests", "schema", "fuzz"]
    assert "started a process" in report.gates[-1].reason
    assert "subprocess.Popen" in report.gates[-1].reason
    assert "tool.py" in report.gates[-1].reason


def test_the_hook_alone_catches_a_banned_call_when_the_static_gate_is_skipped(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["hook_without_static"]

    assert report.failing_gate == "fuzz"
    assert "os.system" in report.gates[-1].reason


def test_reading_outside_the_package_fails_with_the_path_named(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["reads_outside"]

    assert report.failing_gate == "fuzz"
    assert str(_OUTSIDE_FILE) in report.gates[-1].reason


def test_a_tool_that_sleeps_past_the_limit_fails_the_fuzz_gate_as_timed_out(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["sleeps"]

    assert report.failing_gate == "fuzz"
    assert "timed out" in report.gates[-1].reason


def test_a_tool_that_blocks_the_loop_is_cut_by_the_wall_deadline(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["blocks"]

    # Windows has no interval timer, so the runner cannot interrupt a blocking call itself; the
    # Comb's own kill at the wall limit is the only bound there, charged to the resources gate.
    assert report.failing_gate == ("resources" if sys.platform == "win32" else "fuzz")
    assert "wall-clock limit" in report.gates[-1].reason


def test_a_session_exec_the_spec_does_not_declare_fails_the_capabilities_gate(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["hostname_undeclared"]

    assert report.failing_gate == "capabilities"
    assert [gate.name for gate in report.gates][-1] == "capabilities"
    assert "exec:hostname" in report.gates[-1].reason


def test_the_same_exec_passes_once_declared_and_the_session_spawn_is_not_a_violation(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["hostname_declared"]

    assert report.passed
    assert "exec:hostname" not in report.gates[-1].reason


def test_a_test_that_spawns_a_process_fails_the_tests_gate_with_the_violation(
    reports: dict[str, QuarantineReport],
) -> None:
    report = reports["spawning_tests"]

    assert report.failing_gate == "tests"
    assert "started a process" in report.gates[-1].reason
