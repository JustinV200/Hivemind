"""Define authorize and refusal_text: a Worker's tools ask the Guard before they act.

Roadmap step 10.3 (ADR-0031) wires a Worker's own enforcement points through the Guard's `Enforcer`,
which its Warden hands down on `WorkerContext.enforcer`: `tool_invocation` (a tool must be held as
`tool:<name>` before it runs, and the HTTP tool's `net:<host>`), `session_outside_scratch` (a read
outside scratch needs `fs:read:<path>`; the Capping gate's allowlist refusals are recorded here too)
and `question_routing` (the `ask` tool needs `question:human`). Every tool asks the same way: the
Worker is the principal (its own id, under the policy role its assignment names), its own set is
what it holds, and the Cell it runs on is the context. `authorize` builds that request and returns
the Enforcer's decision (a refusal is already a `guard.denied` row by then); `refusal_text` turns a
refusal into the one-line result a model reads, so a refused call is never a silent no-op and never
an exception; every such line starts with `GUARD_REFUSAL_PREFIX`, the fixed token the Drone's
outcome records read a Guard refusal by.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Called by
    `hivemind.workers.tools.registry`, `.http`, `.ask`, `.session` and `.proposals`. Calls into
    `hivemind.guard` and `hivemind.workers.tools.registry` (ToolInvocation) only.

Key invariants:
    - The principal is always the Worker itself and the held set always its own
      `WorkerContext.capabilities`: a tool can never borrow its Warden's wider set.
    - `authorize` records nothing on an allow (the action's own event does that) and never raises
      on a refusal.

See Also:
    - docs/adr/0031-capability-model-attenuation-and-enforcement-points.md for the points.
    - hivemind.guard.enforcer for Enforcer, the adapter every call here goes through.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hivemind.guard import (
    Capability,
    EnforcementPoint,
    PolicyContext,
    PolicyDecision,
    PolicyRequest,
    worker_principal,
)

if TYPE_CHECKING:
    # Only for the annotations: the registry imports this module back for its own check.
    from hivemind.workers.tools.registry import ToolInvocation

# Every refusal line starts with this fixed, machine-produced token (never model prose), so a
# reader such as `hivemind.workers.roles.drone.outcome.records` can classify it by exact prefix.
GUARD_REFUSAL_PREFIX = "refused by the Guard "

__all__ = ["GUARD_REFUSAL_PREFIX", "authorize", "refusal_text"]


async def authorize(
    invocation: ToolInvocation, point: EnforcementPoint, needed: Capability
) -> PolicyDecision:
    """Ask the Guard whether this Worker may take an action needing `needed` at `point`.

    Args:
        invocation: This attempt's context (its Enforcer, set and Cell) and assignment (its role).
        point: The enforcement point the calling tool is passing.
        needed: The one capability the action needs.

    Returns:
        The Enforcer's decision; a refusal is already `guard.denied` on the trail.
    """
    ctx = invocation.ctx
    request = PolicyRequest(
        principal=worker_principal(ctx.worker_id, invocation.assignment.role),
        point=point,
        needed=needed,
        held=ctx.capabilities,
        context=PolicyContext(comb_shield=ctx.cell.comb_shield, access_level=ctx.cell.access_level),
    )
    return await ctx.enforcer.check(request)


def refusal_text(decision: PolicyDecision) -> str:
    """Render a refusal as the tool-result line a model reads: what was refused, and why.

    Args:
        decision: A refused PolicyDecision.

    Returns:
        One sentence naming the Guard's own reason; the action did not happen.
    """
    return f"{GUARD_REFUSAL_PREFIX}({decision.rule}): {decision.reason} Nothing was done."
