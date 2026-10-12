"""Provide the fuzz gate: schema-derived inputs must give a JSON value or a clean raise.

The runner calls the tool's `run` with inputs derived from its own schema (edge values for every
property, required ones always present, optional ones sometimes absent), each under a wall-clock
bound. An honest tool returns a JSON-serialisable value or raises an ordinary exception (a bad
value is the tool's to refuse); it never hangs, never raises something that is not an exception,
and never returns an object that cannot be serialised. This gate turns the runner's `fuzz`
section into a verdict. It is also where a run-time forbidden action (a process spawn that the
static scan could not see) usually surfaces, since fuzzing is what drives `run`.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Fourth gate in `default_gates()`. Reads `ctx.runner` only. Calls into `gates.base`.

Key invariants:
    - Pure over the runner's report.
    - A missing section fails the gate.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the fuzz gate.
    - hivemind.royal_jelly.quarantine_comb.runner.inputs for how inputs are derived.
"""

from __future__ import annotations

from hivemind.royal_jelly.quarantine_comb.gates.base import (
    GateContext,
    describe,
    fail,
    ok,
    violations_for,
)
from hivemind.royal_jelly.spec import GateResult

__all__ = ["FuzzGate"]


class FuzzGate:
    """Require every fuzz case to return a JSON value or raise cleanly, never hang."""

    name = "fuzz"
    uses_runner = True

    async def run(self, ctx: GateContext) -> GateResult:
        """Judge the runner's `fuzz` section.

        Args:
            ctx: The run so far, with `runner` set.

        Returns:
            Passing when every case returned a JSON value or raised an ordinary exception.
        """
        runner = ctx.runner
        if runner is None or runner.fuzz is None:
            return fail(self.name, "fuzzing did not finish within the wall-clock limit")
        broke = violations_for(runner, self.name)
        if broke:
            lines = "\n".join(describe(v) for v in broke)
            return fail(self.name, f"the tool broke a sandbox rule: {describe(broke[0])}", lines)
        section = runner.fuzz
        if section.failure:
            return fail(self.name, section.failure)
        raises = ", ".join(
            f"{name} x{count}" for name, count in sorted(section.clean_raises.items())
        )
        return ok(self.name, f"{section.cases} cases; clean raises: {raises or 'none'}")
