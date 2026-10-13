"""Provide the resources gate: CPU, memory, wall time and output stay under the limits.

The runner measures what the tool's phases spent, net of its own start-up, and the Comb adds the
bytes the sandbox captured on the real stdout and stderr. This gate compares each figure to
`QuarantineLimits`. The operating system enforces the CPU, memory and file-size limits as hard
caps (`resource.setrlimit`), so a tool that blows through them is usually killed outright, which
the Comb reports on this gate too; this gate is the verdict for the tool that stays under the hard
cap but over the budget. A reached output cap counts as over, because the sandbox truncates there
and a tool that fills it has exceeded it.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Fifth gate in `default_gates()`. Reads `ctx.runner` and `ctx.limits`. Calls into `gates.base`.

Key invariants:
    - Pure over the runner's report and the limits.
    - The first exceeded figure is the reason; every figure is in the detail.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the resources gate.
    - hivemind.royal_jelly.quarantine_comb.limits for how the hard caps are derived.
"""

from __future__ import annotations

from hivemind.royal_jelly.quarantine_comb.gates.base import GateContext, fail, ok
from hivemind.royal_jelly.spec import GateResult

__all__ = ["ResourcesGate"]


class ResourcesGate:
    """Require the run's measured usage to fit inside the limits."""

    name = "resources"
    uses_runner = True

    async def run(self, ctx: GateContext) -> GateResult:
        """Compare the runner's `usage` to `ctx.limits`.

        Args:
            ctx: The run so far, with `runner` set.

        Returns:
            Passing when CPU, memory, wall time and output are all within budget.
        """
        if ctx.runner is None:
            return fail(self.name, "the runner produced no usage figures")
        usage, limits = ctx.runner.usage, ctx.limits
        if usage.timed_out_phase:
            return fail(
                self.name, f"wall-clock limit reached during the {usage.timed_out_phase} phase"
            )
        checks = (
            (
                usage.cpu_s > limits.cpu_s,
                f"CPU {usage.cpu_s:.1f}s exceeds the {limits.cpu_s:g}s limit",
            ),
            (
                usage.peak_rss_bytes > limits.memory_bytes,
                f"memory {usage.peak_rss_bytes} bytes exceeds the {limits.memory_bytes} byte limit",
            ),
            (
                usage.wall_s > limits.wall_s,
                f"wall time {usage.wall_s:.1f}s exceeds {limits.wall_s:g}s",
            ),
            (
                usage.output_bytes >= limits.output_bytes,
                f"output {usage.output_bytes} bytes reached the {limits.output_bytes} byte limit",
            ),
        )
        exceeded = [message for over, message in checks if over]
        if exceeded:
            return fail(self.name, exceeded[0], "\n".join(exceeded))
        return ok(
            self.name,
            f"cpu {usage.cpu_s:.2f}s, rss {usage.peak_rss_bytes} bytes, wall {usage.wall_s:.2f}s",
        )
