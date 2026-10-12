"""Define the Gate protocol and GateContext: what every Quarantine Comb check reads and returns.

The Comb is a fixed list of gates run in order. A `Gate` has a name (one of `GATE_ORDER`) and one
async method that reads a `GateContext` and returns a `GateResult`. The context holds everything a
gate may look at: the spec, the unpacked files, the package bytes, the sandbox, the limits, and,
once the runner has run inside the sandbox, the runner's parsed report. Gates are pure readers of
that context (the static gate reads only the files; the other five read the runner's report), so
they can be tested with hand-written reports and no sandbox at all.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Implemented by the six modules beside this one; built and driven by `QuarantineComb`. Calls
    into `hivemind.royal_jelly.spec` for the models.

Key invariants:
    - A gate returns a `GateResult` named after itself; it never raises for a bad tool, only for
      a bug in the gate.
    - `reason` is one bounded sentence naming what failed; `detail` is a bounded excerpt, never
      the tool's source or raw output.
    - A gate with `uses_runner` True is handed a context whose `runner` is set.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the six gates and their order.
    - hivemind.royal_jelly.spec.report for GateResult and QuarantineLimits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from hivemind.royal_jelly.quarantine_comb.runner.report import RunnerReport, Violation
from hivemind.royal_jelly.quarantine_comb.sandbox import Sandbox
from hivemind.royal_jelly.spec import GateResult, QuarantineLimits, ToolSpec
from waggle.messages.base import MAX_REASON_CHARS

MAX_DETAIL_CHARS = 16_384  # GateResult.detail's own bound, restated so gates can truncate to it.

# Which gate owns a violation recorded in a phase: the gate that judges that phase. A violation in
# a phase the table does not know (a future one) falls to the last gate, so it is never lost.
_PHASE_OWNER = {"load": "tests", "tests": "tests", "schema": "schema", "fuzz": "fuzz"}
_LAST_GATE = "capabilities"

__all__ = [
    "MAX_DETAIL_CHARS",
    "Gate",
    "GateContext",
    "bounded",
    "describe",
    "fail",
    "ok",
    "violations_for",
]


@dataclass(frozen=True, slots=True)
class GateContext:
    """Everything a gate may read about one Comb run.

    Attributes:
        spec: The spec the package claims to implement.
        files: The unpacked package (path to bytes).
        package: The package archive bytes, as hashed and shipped.
        sandbox: Where the runner executes; gates do not use it directly today but a gate that
            needs one more probe may.
        limits: What the run may spend.
        runner: The runner's parsed report; None until the Comb has run the runner.
    """

    spec: ToolSpec
    files: Mapping[str, bytes]
    package: bytes
    sandbox: Sandbox
    limits: QuarantineLimits
    runner: RunnerReport | None = None


class Gate(Protocol):
    """One check in the Quarantine Comb's fixed sequence."""

    @property
    def name(self) -> str:
        """The gate's name; one of `GATE_ORDER`, and the name its result carries."""
        ...

    @property
    def uses_runner(self) -> bool:
        """Whether the gate reads the runner's report (so the Comb runs the runner first)."""
        ...

    async def run(self, ctx: GateContext) -> GateResult:
        """Judge the package.

        Args:
            ctx: The run so far; `ctx.runner` is set when `uses_runner` is True.

        Returns:
            The verdict, named after this gate.
        """
        ...


def bounded(text: str, limit: int) -> str:
    """Cut `text` to at most `limit` characters, marking a cut with an ellipsis.

    Args:
        text: Any text, possibly derived from untrusted output.
        limit: The most characters to keep.

    Returns:
        `text` itself when it fits, else its head plus an ellipsis, `limit` characters in all.
    """
    return text if len(text) <= limit else text[: limit - 1] + "…"


def ok(name: str, detail: str = "") -> GateResult:
    """Build a passing result for gate `name`.

    Args:
        name: The gate's name.
        detail: Optional supporting text (bounded here).

    Returns:
        A passing `GateResult`.
    """
    return GateResult(name=name, passed=True, reason="", detail=bounded(detail, MAX_DETAIL_CHARS))


def fail(name: str, reason: str, detail: str = "") -> GateResult:
    """Build a failing result for gate `name`, bounding both texts to the report's limits.

    Args:
        name: The gate's name.
        reason: One sentence on what failed.
        detail: Optional supporting excerpt.

    Returns:
        A failing `GateResult`.
    """
    return GateResult(
        name=name,
        passed=False,
        reason=bounded(reason, MAX_REASON_CHARS),
        detail=bounded(detail, MAX_DETAIL_CHARS),
    )


def violations_for(runner: RunnerReport, gate: str) -> tuple[Violation, ...]:
    """Return the violations the audit hook recorded in the phase `gate` judges.

    Args:
        runner: The runner's parsed report.
        gate: A gate name.

    Returns:
        Violations whose phase maps to `gate`, in the order they happened.
    """
    return tuple(v for v in runner.violations if _PHASE_OWNER.get(v.phase, _LAST_GATE) == gate)


def describe(violation: Violation) -> str:
    """Render a violation as one line naming what was attempted and where in the tool."""
    where = f" at {violation.location}" if violation.location else ""
    return f"{violation.detail}{where}"
