"""Provide the schema gate: the input schema compiles and refuses what it should.

A tool's `input_schema_json` is what a Worker's model reads to build arguments and what the Worker
runtime validates them against before every call. The runner derives one sample input from the
schema and checks that `validate_arguments` accepts it, then checks that the same input with an
extra property is refused (the schema must close the object). This gate turns that section into a
verdict, so a schema that accepts everything, or rejects its own example, never reaches a
registry.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Third gate in `default_gates()`. Reads `ctx.runner` only. Calls into `gates.base`.

Key invariants:
    - Pure over the runner's report.
    - A missing section fails the gate.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the schema gate.
    - hivemind.royal_jelly.quarantine_comb.runner.phases for how the section is produced.
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

__all__ = ["SchemaGate"]


class SchemaGate:
    """Require the schema to compile, accept a sample and refuse an extra property."""

    name = "schema"
    uses_runner = True

    async def run(self, ctx: GateContext) -> GateResult:
        """Judge the runner's `schema` section.

        Args:
            ctx: The run so far, with `runner` set.

        Returns:
            Passing when the schema compiled, a sample validated and an extra property failed.
        """
        runner = ctx.runner
        if runner is None or runner.schema_check is None:
            return fail(self.name, "the schema check did not finish within the wall-clock limit")
        broke = violations_for(runner, self.name)
        if broke:
            return fail(self.name, f"the tool broke a sandbox rule: {describe(broke[0])}")
        section = runner.schema_check
        findings = "\n".join(section.findings)
        if not section.compiled:
            return fail(self.name, "the input schema does not compile", findings)
        if not section.sample_accepted:
            return fail(self.name, "a sample input derived from the schema was refused", findings)
        if not section.extra_refused:
            reason = "an input with an extra property was accepted: set additionalProperties false"
            return fail(self.name, reason, findings)
        return ok(self.name)
