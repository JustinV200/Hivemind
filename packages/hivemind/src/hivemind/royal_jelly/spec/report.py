"""Define the Quarantine Comb's report: gate results, resource usage, limits and the verdict.

The Quarantine Comb is the sandbox every new tool must pass before it is promoted. It runs six
gates in a fixed order (static, tests, schema, fuzz, resources, capabilities); the first failing
gate ends the run. `QuarantineReport` is the one document that run produces: it names the spec and
package it proved by hash, which path through the Comb proved them, each gate's result and what the
run cost. The Comb Registry (the catalogue of promoted tools) refuses to promote without a passing
report whose hashes match the spec and package offered, so the report is the evidence a promotion
stands on. `QuarantineLimits` is what the run was allowed to spend.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.spec. Built by
    the Quarantine Comb (and its in-sandbox runner), carried over Waggle as `report_json`, stored
    in the registry row as evidence and read back by a rollback. Calls into waggle's bounds only.

Key invariants:
    - `passed` equals `all(gate.passed)`, and `failing_gate` is the first failed gate's name (or
      None when every gate passed); a report cannot claim otherwise.
    - A passing report holds exactly the six gates, in `GATE_ORDER`: a pass with a gate skipped is
      not a pass.
    - Frozen and extras forbidden; `to_json`/`from_json` round-trip to an equal report.

See Also:
    - docs/adr/0047 (the Quarantine Comb's gates and sandbox) for the gates and the report.
    - docs/adr/0046 (tool spec, Comb Registry and scopes) for how the registry uses a report.
    - waggle.messages.tool.authoring for QuarantinePath, the route a report records.
"""

from __future__ import annotations

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from waggle.messages.base import (
    MAX_REASON_CHARS,
    SHA256_PATTERN,
    CellIdField,
    ToolIdField,
    UtcDatetime,
)
from waggle.messages.tool.authoring import (
    MAX_VERSION_CHARS,
    MIN_PACKAGE_SIZE_BYTES,
    TOOL_NAME_PATTERN,
    VERSION_PATTERN,
    QuarantinePath,
)

GATE_ORDER = ("static", "tests", "schema", "fuzz", "resources", "capabilities")  # ADR-0047.
MAX_GATES = 16  # Six today; room for a gate the Comb adds without a report-format change.
MAX_GATE_NAME_CHARS = 64  # A gate's short name.
MAX_GATE_DETAIL_CHARS = 16_384  # Test output or a traceback tail: a page or two, never a log.
_MIB = 1024 * 1024  # One mebibyte, the unit the default limits are written in.

__all__ = [
    "GATE_ORDER",
    "GateResult",
    "QuarantineLimits",
    "QuarantineReport",
    "ResourceUsage",
]

_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")  # codingrules 8.5.


class GateResult(BaseModel):
    """One gate's verdict: whether it passed and, if not (or if useful), why."""

    model_config = _MODEL_CONFIG

    name: str = Field(
        min_length=1, max_length=MAX_GATE_NAME_CHARS, description="The gate (static, tests, ...)."
    )
    passed: bool = Field(description="Whether the tool cleared this gate.")
    reason: str = Field(
        default="", max_length=MAX_REASON_CHARS, description="One sentence on why it failed."
    )
    detail: str = Field(
        default="",
        max_length=MAX_GATE_DETAIL_CHARS,
        description="Supporting output (test run, offending line); may be empty.",
    )


class ResourceUsage(BaseModel):
    """What a Comb run actually spent, measured inside the sandbox."""

    model_config = _MODEL_CONFIG

    cpu_s: float = Field(ge=0, description="CPU seconds the tool's process used.")
    peak_rss_bytes: int = Field(ge=0, description="Peak resident memory, in bytes.")
    wall_s: float = Field(ge=0, description="Wall-clock seconds the run took.")
    output_bytes: int = Field(ge=0, description="Bytes the tool wrote to stdout and stderr.")


class QuarantineLimits(BaseModel):
    """What a Comb run may spend; the `resources` gate fails a tool that exceeds any of it."""

    model_config = _MODEL_CONFIG

    cpu_s: float = Field(default=30.0, gt=0, le=3_600, description="CPU seconds allowed.")
    memory_bytes: int = Field(
        default=512 * _MIB, ge=16 * _MIB, le=8_192 * _MIB, description="Resident memory allowed."
    )
    wall_s: float = Field(default=120.0, gt=0, le=3_600, description="Wall-clock seconds allowed.")
    output_bytes: int = Field(
        default=_MIB, ge=1_024, le=64 * _MIB, description="Stdout and stderr bytes allowed."
    )
    fuzz_cases: int = Field(default=32, ge=1, le=1_000, description="Schema-derived fuzz inputs.")

    def to_json(self) -> str:
        """Render the limits as compact JSON (the wire's `limits_json`)."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse limits from JSON text; raises pydantic.ValidationError when malformed."""
        return cls.model_validate_json(text)


class QuarantineReport(BaseModel):
    """The Comb's verdict on one spec and one package: the evidence a promotion stands on.

    Crosses Waggle as `report_json` and is stored verbatim in the Comb Registry row it justifies.
    """

    model_config = _MODEL_CONFIG

    tool_id: ToolIdField = Field(description="The tool this run proved.")
    name: str = Field(pattern=TOOL_NAME_PATTERN, description="The spec's tool name.")
    version: str = Field(
        max_length=MAX_VERSION_CHARS, pattern=VERSION_PATTERN, description="The spec's version."
    )
    spec_sha256: str = Field(pattern=SHA256_PATTERN, description="Canonical hash of the spec.")
    package_sha256: str = Field(pattern=SHA256_PATTERN, description="Hash of the package bytes.")
    package_size_bytes: int = Field(
        ge=MIN_PACKAGE_SIZE_BYTES, description="Size of the package bytes the run proved."
    )
    path: QuarantinePath = Field(description="Which route through the Comb proved the tool.")
    sandbox_cell_id: CellIdField | None = Field(
        description="The sandbox Virtual Cell the run happened in; None for the dev subprocess "
        "sandbox, which has no Cell."
    )
    started_at: UtcDatetime = Field(description="When the run began.")
    finished_at: UtcDatetime = Field(description="When the run ended; not before started_at.")
    gates: tuple[GateResult, ...] = Field(
        min_length=1, max_length=MAX_GATES, description="Each gate that ran, in order."
    )
    passed: bool = Field(description="True exactly when every gate passed.")
    failing_gate: Annotated[str, Field(min_length=1, max_length=MAX_GATE_NAME_CHARS)] | None = (
        Field(description="Name of the first failed gate; None when the report passed.")
    )
    usage: ResourceUsage = Field(description="What the run spent.")
    reviewed: bool = Field(
        default=False,
        description="True once the Queen's awake review accepted a REAL_CELL-path report.",
    )

    @model_validator(mode="after")
    def _verdict_agrees_with_gates(self) -> Self:
        """Require passed, failing_gate and the gate list to tell one story."""
        # The verdict is derived data; storing it is for readers, so it must not be able to
        # contradict the gates it summarises.
        failed = next((gate.name for gate in self.gates if not gate.passed), None)
        if self.passed != (failed is None):
            raise ValueError("QuarantineReport passed must equal all(gate.passed).")
        if self.failing_gate != failed:
            raise ValueError("QuarantineReport failing_gate must be the first failed gate's name.")
        names = tuple(gate.name for gate in self.gates)
        if len(set(names)) != len(names):
            raise ValueError("QuarantineReport gate names must be unique.")
        # A pass with a gate skipped would promote a tool nobody fully tested.
        if self.passed and names != GATE_ORDER:
            raise ValueError(
                f"A passing QuarantineReport must hold exactly the gates {GATE_ORDER}."
            )
        if self.finished_at < self.started_at:
            raise ValueError("QuarantineReport finished_at must not precede started_at.")
        return self

    def to_json(self) -> str:
        """Render the report as JSON text, the form carried over Waggle and stored in the row."""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Parse a report from `to_json` text; raises pydantic.ValidationError when malformed."""
        return cls.model_validate_json(text)
