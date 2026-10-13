"""Provide the tests gate: the tool's own `unittest` suite must pass inside the sandbox.

The runner executes `test_tool.py` with `unittest` inside the sandbox and records how many tests
ran and which failed. This gate turns that section into a verdict. The tool's tests are written by
the same model that wrote the tool, so passing them proves little alone (the other gates exist for
that reason), but failing them proves something is wrong, and running none proves nothing was
tried. A forbidden operating-system action the audit hook caught while the tool loaded or while
its tests ran fails this gate first, naming the action, because it explains the failure better
than the exception the hook raised to stop it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Second gate in `default_gates()`. Reads `ctx.runner` only. Calls into `gates.base`.

Key invariants:
    - Pure over the runner's report: it never runs anything.
    - A missing section (the wall deadline fired first) fails the gate.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the tests gate.
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

__all__ = ["TestsGate"]


class TestsGate:
    """Require the tool's `unittest` tests to run and pass."""

    __test__ = False  # Not a pytest class despite the name: pytest must not try to collect it.

    name = "tests"
    uses_runner = True

    async def run(self, ctx: GateContext) -> GateResult:
        """Judge the runner's `tests` section.

        Args:
            ctx: The run so far, with `runner` set.

        Returns:
            Passing when at least one test ran and none failed or errored.
        """
        runner = ctx.runner
        if runner is None or runner.tests is None:
            return fail(self.name, "the tool's tests did not finish within the wall-clock limit")
        broke = violations_for(runner, self.name)
        if broke:
            lines = "\n".join(describe(v) for v in broke)
            return fail(self.name, f"the tool broke a sandbox rule: {describe(broke[0])}", lines)
        section = runner.tests
        if section.error:
            return fail(self.name, section.error, section.output_tail)
        if section.ran == 0:
            return fail(self.name, "test_tool.py ran no tests", section.output_tail)
        if section.failures:
            reason = f"{len(section.failures)} of {section.ran} tests failed: {section.failures[0]}"
            return fail(self.name, reason, "\n".join(section.failures) + "\n" + section.output_tail)
        return ok(self.name, f"{section.ran} tests passed")
