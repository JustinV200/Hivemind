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
from collections.abc import Mapping
from datetime import timedelta

from hivemind.royal_jelly.registry import PromotionOffer
from hivemind.royal_jelly.spec import (
    GATE_ORDER,
    SPEC_FILE,
    TESTS_MODULE,
    TOOL_MODULE,
    GateResult,
    QuarantineReport,
    ResourceUsage,
    ToolSpec,
    pack,
    sha256_of,
    spec_sha256,
)
from waggle.clock import Clock, FakeClock
from waggle.ids import new_cell_id, new_tool_id, new_warden_id
from waggle.messages.labels import OsFamily
from waggle.messages.tool.authoring import QuarantinePath, ToolScope

__all__ = [
    "DEFAULT_SCHEMA",
    "make_failing_report",
    "make_offer",
    "make_package",
    "make_package_files",
    "make_passing_report",
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
