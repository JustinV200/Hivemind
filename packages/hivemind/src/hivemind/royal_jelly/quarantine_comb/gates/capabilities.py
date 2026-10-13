"""Provide the capabilities gate: every session call the tool made must be one its spec declares.

A tool acts only through the `CellSession` it is handed, and what it may do through it is the
spec's `required_capabilities` (`exec:<command>`, `fs:read:<path>`, `fs:write:<path>`). The runner
hands the tool a recording session and logs each call; this gate maps every recorded call to the
capability that call needs and checks the declared set allows it, using the Guard's own capability
check, so a tool cannot do at run time what it did not ask for at request time. The first
uncovered call fails the gate and is named in the reason. Paths are the scratch-relative ones the
tool passed, so a declaration like `fs:read:*` or `fs:write:reports/**` is portable across Cells.

Fits into the Hive:
    Layer 3 (sources of Cells, and capabilities handed down), inside royal_jelly.quarantine_comb.
    Last gate in `default_gates()`. Reads `ctx.runner` and `ctx.spec`. Calls into
    `hivemind.guard` (Capability, CapabilitySet) and `gates.base`.

Key invariants:
    - Pure over the runner's report and the spec.
    - A call with no recorded capability mapping fails the gate rather than passing silently.
    - Violations the audit hook recorded outside the earlier phases fail this gate.

See Also:
    - docs/adr/0047 (the Comb's gates and sandbox) for the capabilities gate.
    - hivemind.guard.capabilities for the capability grammar and matching.
"""

from __future__ import annotations

from hivemind.guard import (
    Capability,
    CapabilityFamily,
    CapabilitySet,
    GuardError,
    glob_literal,
)
from hivemind.royal_jelly.quarantine_comb.gates.base import (
    GateContext,
    describe,
    fail,
    ok,
    violations_for,
)
from hivemind.royal_jelly.quarantine_comb.runner.report import SessionCall
from hivemind.royal_jelly.spec import GateResult

# Session operation to the capability family it needs; `start` runs a command like `exec` does.
_FAMILY_OF_OP = {
    "exec": CapabilityFamily.EXEC,
    "start": CapabilityFamily.EXEC,
    "get_file": CapabilityFamily.FS_READ,
    "put_file": CapabilityFamily.FS_WRITE,
    "delete_file": CapabilityFamily.FS_WRITE,
}

__all__ = ["CapabilitiesGate", "capability_text"]


class CapabilitiesGate:
    """Require every recorded session call to be covered by the declared capabilities."""

    name = "capabilities"
    uses_runner = True

    async def run(self, ctx: GateContext) -> GateResult:
        """Map the recorded calls to capabilities and check the declared set allows each.

        Args:
            ctx: The run so far, with `runner` set.

        Returns:
            Passing when every call is covered; failing at the first that is not.
        """
        if ctx.runner is None:
            return fail(self.name, "the runner produced no session-call log")
        stray = violations_for(ctx.runner, self.name)
        if stray:
            return fail(self.name, f"the tool broke a sandbox rule: {describe(stray[0])}")
        try:
            declared = CapabilitySet.parse(*ctx.spec.required_capabilities)
        except GuardError as error:
            return fail(self.name, f"the spec declares an invalid capability: {error}")
        for call in ctx.runner.calls:
            needed = _needed(call)
            if needed is None:
                return fail(self.name, f"the tool made an unrecognised session call: {call.op}")
            if not declared.allows(needed):
                reason = f"the tool called {call.op} needing `{capability_text(call)}`, which "
                return fail(self.name, reason + "its declared capabilities do not cover")
        return ok(self.name, f"{len(ctx.runner.calls)} distinct session calls, all covered")


def capability_text(call: SessionCall) -> str:
    """Render the capability a recorded call needs, as the spec would declare it.

    Args:
        call: One recorded session call.

    Returns:
        `exec:ls`, `fs:read:notes.txt` or `fs:write:out/report.txt`; the op name if unmapped.
    """
    family = _FAMILY_OF_OP.get(call.op)
    return call.op if family is None else f"{family.value}:{call.target}"


def _needed(call: SessionCall) -> Capability | None:
    """Build the Capability a call needs, escaping glob characters in its target.

    A path like `a*b` must be needed literally, not read as a pattern a held scope could be
    mistaken to cover by accident.
    """
    family = _FAMILY_OF_OP.get(call.op)
    if family is None or not call.target:
        return None
    return Capability(family=family, scope=glob_literal(call.target))
