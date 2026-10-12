"""Define the runner's report: the one JSON document the in-sandbox runner writes for the Comb.

The runner executes inside the sandbox and does the work that needs the tool's code to run: its
tests, a schema check, schema-derived fuzzing, resource measurement, a watch for forbidden
operating-system calls and a log of every session call. It cannot decide anything; it measures and
writes one document, and the Comb's gates read that document and turn each section into a verdict.
The document crosses a trust boundary (it is produced next to untrusted code), so the models here
are frozen, reject unknown fields and bound every list and string the Comb will read back.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Written by `runner.main`, read by `QuarantineComb` and parsed into `GateContext.runner` for
    the tests, schema, fuzz, resources and capabilities gates. Calls into pydantic only.

Key invariants:
    - The JSON keys are exactly `tests`, `schema`, `fuzz`, `usage`, `violations`, `calls`
      (`schema` is aliased because pydantic reserves that attribute name).
    - A section is None when its phase never finished (the wall deadline fired first); a gate
      treats a missing section as a failure of that gate.
    - Every violation and call carries bounded text only; the runner never copies tool output or
      source into the report beyond short excerpts (codingrules section 12).

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for what the runner records and why.
    - hivemind.royal_jelly.quarantine_comb.gates for the readers of this document.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_TEXT_CHARS = 2_000  # One excerpt: enough to name a construct or a failure, never a log.
MAX_LIST_ITEMS = 64  # One list in the document: failures, violations, distinct session calls.
MAX_TAIL_CHARS = 4_000  # The tail of a test run's own output kept for a human reading the gate.

__all__ = [
    "MAX_LIST_ITEMS",
    "MAX_TAIL_CHARS",
    "MAX_TEXT_CHARS",
    "FuzzSection",
    "RunnerReport",
    "SchemaSection",
    "SessionCall",
    "UnittestSection",
    "UsageSection",
    "Violation",
]

_CONFIG = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)  # codingrules 8.5.


class UnittestSection(BaseModel):
    """What the tool's own `unittest` run did."""

    model_config = _CONFIG

    ran: int = Field(ge=0, description="Tests that ran.")
    failures: tuple[str, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="One short line per failed or errored test."
    )
    error: str = Field(
        default="", max_length=MAX_TEXT_CHARS, description="Why no run happened (an import error)."
    )
    output_tail: str = Field(
        default="", max_length=MAX_TAIL_CHARS, description="Tail of unittest's own report text."
    )


class SchemaSection(BaseModel):
    """What the schema check found."""

    model_config = _CONFIG

    compiled: bool = Field(description="The schema parsed and `validate_arguments` ran on it.")
    sample_accepted: bool = Field(description="A sample input derived from the schema validated.")
    extra_refused: bool = Field(description="The sample plus an extra property was refused.")
    findings: tuple[str, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="Validation messages that explain a failure."
    )


class FuzzSection(BaseModel):
    """What fuzzing the tool with schema-derived inputs found."""

    model_config = _CONFIG

    cases: int = Field(ge=0, description="Inputs that were tried.")
    clean_raises: dict[str, int] = Field(
        max_length=MAX_LIST_ITEMS, description="Exception class name to how often it was raised."
    )
    failure: str = Field(
        default="", max_length=MAX_TEXT_CHARS, description="Why fuzzing failed; empty when it held."
    )


class UsageSection(BaseModel):
    """What the run spent, measured net of the runner's own start-up."""

    model_config = _CONFIG

    cpu_s: float = Field(ge=0, description="CPU seconds the tool's phases used.")
    peak_rss_bytes: int = Field(ge=0, description="Resident memory the tool's phases added.")
    wall_s: float = Field(ge=0, description="Wall seconds from the first phase to the last.")
    output_bytes: int = Field(ge=0, description="Bytes the tool wrote to stdout and stderr.")
    timed_out_phase: str = Field(
        default="", max_length=64, description="The phase the wall deadline interrupted, if any."
    )


class Violation(BaseModel):
    """One forbidden operating-system action the audit hook attributed to the tool."""

    model_config = _CONFIG

    kind: str = Field(max_length=64, description="Short class: socket, process, exec, open.")
    detail: str = Field(max_length=MAX_TEXT_CHARS, description="What was attempted, with the path.")
    phase: str = Field(max_length=64, description="Which phase it happened in (load, tests, ...).")
    location: str = Field(max_length=MAX_TEXT_CHARS, description="File and line in the tool.")


class SessionCall(BaseModel):
    """One distinct call the tool made on its session, with how often."""

    model_config = _CONFIG

    op: str = Field(max_length=32, description="exec, start, put_file, get_file or delete_file.")
    target: str = Field(
        max_length=MAX_TEXT_CHARS, description="argv[0] or a scratch-relative path."
    )
    count: int = Field(ge=1, description="How many times this call was made.")


class RunnerReport(BaseModel):
    """The whole document: one optional section per phase, plus violations and session calls."""

    model_config = _CONFIG

    tests: UnittestSection | None = Field(description="None when the deadline fired first.")
    schema_check: SchemaSection | None = Field(alias="schema", description="Likewise.")
    fuzz: FuzzSection | None = Field(description="Likewise.")
    usage: UsageSection = Field(description="What the run spent.")
    violations: tuple[Violation, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="Forbidden actions attributed to the tool."
    )
    calls: tuple[SessionCall, ...] = Field(
        max_length=MAX_LIST_ITEMS, description="Distinct session calls the tool made."
    )
