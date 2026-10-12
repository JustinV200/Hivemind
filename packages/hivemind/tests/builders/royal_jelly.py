"""Build Royal Jelly test data: a tool spec, a Quarantine Comb report, and a tiny valid package.

The Comb Registry's rules are tested by changing one fact at a time: a spec that asks for a
capability, a report that failed a gate, a package whose hash differs. These builders produce a
valid baseline of each (a tool that echoes its arguments, a report that passed all six gates, the
three-file package that tool is shipped as) and let a test override exactly the field under test.
Everything returned passes the models' own validators; a test that needs an invalid spec uses
`model_copy(update=...)`, which skips them on purpose.

Fits into the Hive:
    Test infrastructure (codingrules section 14.5), not shipped. Used by the royal_jelly unit tests
    and the Comb Registry contract suite; later waves' tests reuse it.

Key invariants:
    - Every builder takes an optional `clock`, so ids and timestamps are deterministic where a
      test cares; ids are otherwise fresh per call.
    - `make_package` always includes `tool.py`, `test_tool.py` and `spec.json` unless the caller
      passes its own mapping.

See Also:
    - hivemind.royal_jelly.spec for the models built here.
    - tests/builders/README.md for the builder conventions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from hivemind.cell.session import CompletedCommand
from hivemind.royal_jelly.quarantine_comb.gates import GateContext
from hivemind.royal_jelly.quarantine_comb.runner.report import RunnerReport
from hivemind.royal_jelly.registry import PromotionOffer, RegistryChange
from hivemind.royal_jelly.scaffold import ScaffoldResult, ToolRequestBrief
from hivemind.royal_jelly.spec import (
    GATE_ORDER,
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    GateResult,
    QuarantineLimits,
    QuarantineReport,
    ResourceUsage,
    ToolSpec,
    pack,
    sha256_of,
    spec_sha256,
)
from waggle.clock import Clock, FakeClock
from waggle.ids import CellId, new_cell_id, new_tool_id, new_warden_id
from waggle.messages.labels import OsFamily
from waggle.messages.reports import PlatformReport
from waggle.messages.tool.authoring import QuarantinePath, ToolRequest, ToolScope

__all__ = [
    "DEFAULT_SCHEMA",
    "FakeSandboxRunner",
    "RecordingAnnouncer",
    "RecordingHoney",
    "make_brief",
    "make_failing_report",
    "make_offer",
    "make_package",
    "make_package_files",
    "make_passing_report",
    "make_platform",
    "make_scaffold_result",
    "make_tool_spec",
]

# A closed object schema: the shape validate_spec demands of every tool.
DEFAULT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "additionalProperties": False,
}
_TOOL_SOURCE = b"async def run(session, arguments):\n    return {'echo': arguments}\n"
_TESTS_SOURCE = (
    b"import unittest\n\n\nclass EchoTest(unittest.TestCase):\n"
    b"    def test_truth(self):\n        self.assertTrue(True)\n"
)
_DEFAULT_PACKAGE_SIZE = 1_024  # A stand-in size for reports built without real package bytes.


def make_tool_spec(clock: Clock | None = None, /, **overrides: object) -> ToolSpec:
    """Build a valid HIVE-scope echo tool spec, with any field overridden.

    Args:
        clock: Mints the ids (positional only); a fresh FakeClock by default.
        overrides: Any `ToolSpec` field. Overriding `scope` to CELL without a `cell_id` mints one.

    Returns:
        A validated spec.
    """
    clock = clock or FakeClock()
    fields: dict[str, object] = {
        "tool_id": new_tool_id(clock),
        "requested_by": new_warden_id(clock),
        "name": "echo_tool",
        "version": "1.0.0",
        "description": "Echo the arguments back to the caller.",
        "input_schema_json": json.dumps(DEFAULT_SCHEMA),
        "required_capabilities": ("net:example.org",),
        "supported_os": (OsFamily.LINUX,),
        "scope": ToolScope.HIVE,
        "cell_id": None,
    }
    fields.update(overrides)
    # A CELL-scope spec needs its Cell; mint one so a test saying only scope=CELL stays short.
    if fields["scope"] is ToolScope.CELL and fields["cell_id"] is None:
        fields["cell_id"] = new_cell_id(clock)
    return ToolSpec.model_validate(fields)


def make_package_files(spec: ToolSpec | None = None) -> dict[str, bytes]:
    """Return the three files of a tiny valid tool, with `spec.json` for `spec`.

    Args:
        spec: The spec to write into `spec.json`; a fresh default spec when omitted.

    Returns:
        Path to content for `tool.py`, `test_tool.py` and `spec.json`.
    """
    spec = spec or make_tool_spec()
    return {
        TOOL_MODULE: _TOOL_SOURCE,
        TESTS_MODULE: _TESTS_SOURCE,
        SPEC_FILE: spec.to_json().encode("utf-8"),
    }


def make_package(files: Mapping[str, bytes] | None = None, spec: ToolSpec | None = None) -> bytes:
    """Pack a tool: the given files, or a tiny valid tool.

    Args:
        files: The files to pack; the default three-file tool when omitted.
        spec: The spec for the default `spec.json`; ignored when `files` is given.

    Returns:
        Deterministic zip bytes.
    """
    return pack(files if files is not None else make_package_files(spec))


def make_offer(
    spec: ToolSpec, *, reviewed: bool = False, **report_overrides: object
) -> tuple[QuarantineReport, PromotionOffer]:
    """Build a real package for `spec`, a passing report for it and the matching offer.

    Args:
        spec: The spec to package and prove.
        reviewed: The offer's review flag.
        report_overrides: Any `QuarantineReport` field (a different `path`, ...).

    Returns:
        The passing report and the `PromotionOffer` whose hash and size agree with it, ready to
        hand to `CombRegistry.promote` together with `spec`.
    """
    package = make_package(spec=spec)
    digest = sha256_of(package)
    report = make_passing_report(spec, digest, len(package), **report_overrides)
    return report, PromotionOffer(digest, len(package), reviewed=reviewed)


def make_passing_report(
    spec: ToolSpec,
    package_sha256: str,
    package_size_bytes: int = _DEFAULT_PACKAGE_SIZE,
    **overrides: object,
) -> QuarantineReport:
    """Build a report in which every gate passed, for `spec` and the package hash.

    Args:
        spec: The spec the report proves.
        package_sha256: The package hash the report proves.
        package_size_bytes: The package size it records.
        overrides: Any `QuarantineReport` field (a different `path`, `reviewed`, ...).

    Returns:
        A validated, passing report.
    """
    return _report(spec, package_sha256, package_size_bytes, None, overrides)


def make_failing_report(
    spec: ToolSpec,
    package_sha256: str,
    package_size_bytes: int = _DEFAULT_PACKAGE_SIZE,
    *,
    failing_gate: str = "tests",
    **overrides: object,
) -> QuarantineReport:
    """Build a report that stopped at `failing_gate`, the way the Comb does (first failure ends).

    Args:
        spec: The spec the report is about.
        package_sha256: The package hash it is about.
        package_size_bytes: The package size it records.
        failing_gate: Which gate failed; one of `GATE_ORDER`.
        overrides: Any `QuarantineReport` field.

    Returns:
        A validated report with `passed` False.
    """
    return _report(spec, package_sha256, package_size_bytes, failing_gate, overrides)


def _report(
    spec: ToolSpec,
    package_sha256: str,
    package_size_bytes: int,
    failing_gate: str | None,
    overrides: Mapping[str, object],
) -> QuarantineReport:
    """Assemble a report whose gates pass up to `failing_gate` (all of them when None)."""
    clock = FakeClock()
    started = clock.now()
    # Gates run in order and the first failure ends the run, so a failing report holds the passing
    # gates before the failure and the failure itself, nothing after.
    gates: list[GateResult] = []
    for name in GATE_ORDER:
        failed = name == failing_gate
        gates.append(GateResult(name=name, passed=not failed, reason="boom" if failed else ""))
        if failed:
            break
    fields: dict[str, object] = {
        "tool_id": spec.tool_id,
        "name": spec.name,
        "version": spec.version,
        "spec_sha256": spec_sha256(spec),
        "package_sha256": package_sha256,
        "package_size_bytes": package_size_bytes,
        "path": QuarantinePath.SANDBOX_CELL,
        "sandbox_cell_id": new_cell_id(clock),
        "started_at": started,
        "finished_at": started + timedelta(seconds=2),
        "gates": tuple(gates),
        "passed": failing_gate is None,
        "failing_gate": failing_gate,
        "usage": ResourceUsage(cpu_s=0.5, peak_rss_bytes=40_000_000, wall_s=1.5, output_bytes=200),
    }
    fields.update(overrides)
    return QuarantineReport.model_validate(fields)


# ──────────────────────────────────────────────────────────────────────────────
# Quarantine Comb builders (wave 2D): tool sources, malicious packages
# ──────────────────────────────────────────────────────────────────────────────

__all__ += [
    "MALICIOUS_KINDS",
    "StubSandbox",
    "make_gate_context",
    "make_malicious_files",
    "make_runner_report",
    "make_tool_files",
]

_PASSING_TEST_SOURCE = (
    "import unittest\n\n\nclass ToolTest(unittest.TestCase):\n"
    "    def test_truth(self):\n        self.assertTrue(True)\n"
)
_TOOL_HEADER = "async def run(session, arguments):\n"

# Each malicious kind: the `tool.py` source it ships. The tests are the harmless default, so the
# one thing under test is the tool; the dependency kind needs a spec, not a source, and is built
# by the test itself (`make_tool_spec(dependencies=...)`).
_MALICIOUS_TOOLS = {
    "socket_import": "import socket\n\n" + _TOOL_HEADER + "    return {}\n",
    "from_subprocess": "from subprocess import run as shell\n\n" + _TOOL_HEADER + "    return {}\n",
    "os_system": "import os\n\n" + _TOOL_HEADER + "    os.system('true')\n    return {}\n",
    "os_alias": "import os as o\n\n" + _TOOL_HEADER + "    o.system('true')\n    return {}\n",
    "open_passwd": _TOOL_HEADER + "    return {'x': open('/etc/passwd').read()}\n",
    "dunder_import": _TOOL_HEADER + "    return {'m': str(__import__('socket'))}\n",
    "importlib": "import importlib\n\n"
    + _TOOL_HEADER
    + "    return {'m': str(importlib.import_module('socket'))}\n",
    "from_asyncio_subprocess": "from asyncio import create_subprocess_exec\n\n"
    + _TOOL_HEADER
    + "    return {}\n",
    "wrong_entry": "async def execute(session, arguments):\n    return {}\n",
    "sync_entry": "def run(session, arguments):\n    return {}\n",
    "not_stdlib": "import numpy\n\n" + _TOOL_HEADER + "    return {}\n",
}
MALICIOUS_KINDS = (*_MALICIOUS_TOOLS, "no_tests", "extra_file")


def make_tool_files(
    run_source: str, test_source: str | None = None, spec: ToolSpec | None = None
) -> dict[str, bytes]:
    """Return the three package files for a tool whose `tool.py` is exactly `run_source`.

    Args:
        run_source: The whole text of `tool.py`.
        test_source: The whole text of `test_tool.py`; one passing `unittest` test when omitted.
        spec: The spec written to `spec.json`; a fresh default spec when omitted.

    Returns:
        Path to content for `tool.py`, `test_tool.py` and `spec.json`.
    """
    files = make_package_files(spec)
    files[TOOL_MODULE] = run_source.encode("utf-8")
    files[TESTS_MODULE] = (test_source or _PASSING_TEST_SOURCE).encode("utf-8")
    return files


def make_malicious_files(kind: str, spec: ToolSpec | None = None) -> dict[str, bytes]:
    """Return a package that breaks exactly one static rule.

    Args:
        kind: One of `MALICIOUS_KINDS`: a banned import in some spelling, a banned builtin, a
            wrong entry point, a missing test class, an extra file or a package-external import.
        spec: The spec for `spec.json`; a fresh default spec when omitted.

    Returns:
        The package files; everything but the named defect is valid.

    Raises:
        KeyError: `kind` is not one of `MALICIOUS_KINDS`.
    """
    if kind == "no_tests":
        return make_tool_files(
            "async def run(session, arguments):\n    return {}\n",
            "import unittest\n\nVALUE = 1\n",
            spec,
        )
    if kind == "extra_file":
        return {**make_package_files(spec), "extra.py": b"VALUE = 1\n"}
    return make_tool_files(_MALICIOUS_TOOLS[kind], None, spec)


_PASSING_RUNNER_REPORT = {
    "tests": {"ran": 1, "failures": []},
    "schema": {"compiled": True, "sample_accepted": True, "extra_refused": True, "findings": []},
    "fuzz": {"cases": 4, "clean_raises": {}, "failure": ""},
    "usage": {"cpu_s": 0.1, "peak_rss_bytes": 1_000, "wall_s": 0.5, "output_bytes": 0},
    "violations": [],
    "calls": [],
}


def make_runner_report(**overrides: object) -> RunnerReport:
    """Build the runner's report as if every phase passed, with any section replaced.

    Args:
        overrides: Any of `tests`, `schema`, `fuzz`, `usage`, `violations`, `calls`, as plain
            JSON-shaped values (dicts and lists) or None for a phase that never finished.

    Returns:
        A validated `RunnerReport`.
    """
    return RunnerReport.model_validate({**_PASSING_RUNNER_REPORT, **overrides})


@dataclass(slots=True)
class StubSandbox:
    """A Sandbox that runs nothing: it records puts and execs and plays back a scripted report.

    Mutable test double (it records what the Comb did to it); production sandboxes are the two
    real implementations. `exec` returns `result` and, when `report_json` is set, "writes" it to
    `report.json` the way the real runner would.
    """

    report_json: bytes | None = None
    result: CompletedCommand = field(
        default_factory=lambda: CompletedCommand(
            exit_code=0, stdout=b"", stderr=b"", duration_s=0.1
        )
    )
    cell_id: CellId | None = None
    interpreter: str = "python3"
    root: Path = Path("/sandbox")
    files: dict[str, bytes] = field(default_factory=dict)
    execs: list[tuple[str, ...]] = field(default_factory=list)
    cleaned: int = 0

    async def put(self, relative: Path, data: bytes) -> None:
        """Remember `data` at `relative`."""
        self.files[relative.as_posix()] = data

    async def exec(
        self, argv: tuple[str, ...], timeout_s: float, env: Mapping[str, str] | None = None
    ) -> CompletedCommand:
        """Record the command; play back the scripted report and result."""
        self.execs.append(argv)
        if self.report_json is not None:
            self.files["report.json"] = self.report_json
        return self.result

    async def read(self, relative: Path) -> bytes:
        """Return the file or raise `FileNotFoundError` like a real sandbox."""
        try:
            return self.files[relative.as_posix()]
        except KeyError:
            raise FileNotFoundError(relative) from None

    async def cleanup(self) -> None:
        """Count the call."""
        self.cleaned += 1


def make_gate_context(
    spec: ToolSpec | None = None,
    runner: RunnerReport | None = None,
    limits: QuarantineLimits | None = None,
    files: Mapping[str, bytes] | None = None,
) -> GateContext:
    """Build a `GateContext` over a `StubSandbox`, for testing one gate by hand.

    Args:
        spec: The spec; a fresh default one when omitted.
        runner: The runner's parsed report; None for the static gate.
        limits: The limits; the defaults when omitted.
        files: The unpacked package; the default three-file tool for `spec` when omitted.

    Returns:
        A context whose package bytes are the packed `files`.
    """
    spec = spec or make_tool_spec()
    contents = dict(files) if files is not None else make_package_files(spec)
    return GateContext(
        spec=spec,
        files=contents,
        package=pack(contents),
        sandbox=StubSandbox(),
        limits=limits or QuarantineLimits(),
        runner=runner,
    )


__all__ += [
    "FakeSandboxRunner",
    "RecordingAnnouncer",
    "RecordingHoney",
    "make_brief",
    "make_platform",
    "make_scaffold_result",
    "make_tool_request",
]


def make_platform(**overrides: object) -> PlatformReport:
    """Build a valid Ubuntu platform report (the sandbox image's own), with fields overridden."""
    fields: dict[str, object] = {
        "os": OsFamily.LINUX,
        "distribution": "ubuntu",
        "architecture": "x86_64",
        "package_manager": "apt",
        "shell": "/bin/bash",
        "python_version": "3.12.3",
    }
    fields.update(overrides)
    return PlatformReport.model_validate(fields)


def make_brief(clock: Clock | None = None, /, **overrides: object) -> ToolRequestBrief:
    """Build a valid HIVE-scope scaffold brief for the echo tool, with any field overridden.

    Args:
        clock: Mints the ids (positional only); a fresh FakeClock by default.
        overrides: Any `ToolRequestBrief` field. Overriding `scope` to CELL without a `cell_id`
            mints one.

    Returns:
        A validated brief whose capabilities are `net:example.org` and `fs:read:**`.
    """
    clock = clock or FakeClock()
    fields: dict[str, object] = {
        "tool_id": new_tool_id(clock),
        "requested_by": new_warden_id(clock),
        "name": "echo_tool",
        "description": "Echo the arguments back to the caller.",
        "required_capabilities": ("net:example.org", "fs:read:**"),
        "platform": make_platform(),
        "scope": ToolScope.HIVE,
        "cell_id": None,
        "reason": "A step of the task needs an echo.",
    }
    fields.update(overrides)
    if fields["scope"] is ToolScope.CELL and fields["cell_id"] is None:
        fields["cell_id"] = new_cell_id(clock)
    return ToolRequestBrief.model_validate(fields)


def make_tool_request(clock: Clock | None = None, /, **overrides: object) -> ToolRequest:
    """Build a valid `tool.request` for the echo tool on an Ubuntu Cell, with fields overridden."""
    clock = clock or FakeClock()
    fields: dict[str, object] = {
        "tool_id": new_tool_id(clock),
        "warden_id": new_warden_id(clock),
        "worker_id": None,
        "task_id": None,
        "cell_id": new_cell_id(clock),
        "scope": ToolScope.HIVE,
        "name": "echo_tool",
        "description": "Echo the arguments back to the caller.",
        "required_capabilities": ("net:example.org",),
        "platform": make_platform(),
        "reason": "A step of the task needs an echo.",
    }
    fields.update(overrides)
    return ToolRequest.model_validate(fields)


def make_scaffold_result(brief: ToolRequestBrief, **spec_overrides: object) -> ScaffoldResult:
    """Build the result a scaffolder would return for `brief`: its spec, files and package.

    Args:
        brief: The brief; the spec takes its identity from it exactly as `ModelScaffolder` does.
        spec_overrides: Any `ToolSpec` field to change (a different `version`, say).

    Returns:
        A validated result whose package really packs the three files.
    """
    fields: dict[str, object] = {
        "tool_id": brief.tool_id,
        "requested_by": brief.requested_by,
        "name": brief.name,
        "scope": brief.scope,
        "cell_id": brief.cell_id,
        "supported_os": (brief.platform.os,),
        "required_capabilities": brief.required_capabilities[:1],
    }
    fields.update(spec_overrides)
    spec = make_tool_spec(**fields)
    files = make_package_files(spec)
    package = pack(files)
    digest = sha256_of(package)
    return ScaffoldResult(spec=spec, files=files, package=package, package_sha256=digest)


class FakeSandboxRunner:
    """A `SandboxRunner` that answers from a script of verdicts and remembers what it was sent.

    Each entry is `"pass"`, a failing gate's name (`"tests"`), or an exception to raise. The report
    is built over the spec and package the flow actually sent, so its hashes always agree, and
    carries the quarantine path it was asked for. The last entry repeats once the script runs out.
    """

    def __init__(self, *verdicts: str | Exception) -> None:
        """Build the runner over its verdicts (a single `"pass"` when none are given)."""
        self._verdicts: Sequence[str | Exception] = verdicts or ("pass",)
        self.calls: list[tuple[ToolSpec, bytes, QuarantinePath]] = []

    async def quarantine(
        self, spec: ToolSpec, package: bytes, path: QuarantinePath
    ) -> QuarantineReport:
        """Record the call and return (or raise) the scripted verdict for it."""
        self.calls.append((spec, package, path))
        verdict = self._verdicts[min(len(self.calls), len(self._verdicts)) - 1]
        if isinstance(verdict, Exception):
            raise verdict
        digest, size = sha256_of(package), len(package)
        if verdict == "pass":
            return make_passing_report(spec, digest, size, path=path)
        return make_failing_report(spec, digest, size, failing_gate=verdict, path=path)


class RecordingHoney:
    """A `HoneyDeposit` that records what it was asked to describe, or fails on request."""

    def __init__(self, error: Exception | None = None) -> None:
        """Build the fake; `error`, when given, is raised by every deposit."""
        self._error = error
        self.deposits: list[tuple[ToolSpec, QuarantineReport, str]] = []

    async def deposit_promotion(
        self, spec: ToolSpec, report: QuarantineReport, package_sha256: str
    ) -> None:
        """Record the deposit, then raise the configured error if there is one."""
        self.deposits.append((spec, report, package_sha256))
        if self._error is not None:
            raise self._error


class RecordingAnnouncer:
    """An `Announcer` that records each announced change, or fails on request."""

    def __init__(self, error: Exception | None = None) -> None:
        """Build the fake; `error`, when given, is raised by every announcement."""
        self._error = error
        self.changes: list[tuple[RegistryChange, ToolSpec]] = []

    async def announce(self, change: RegistryChange, spec: ToolSpec) -> None:
        """Record the announcement, then raise the configured error if there is one."""
        self.changes.append((change, spec))
        if self._error is not None:
            raise self._error
