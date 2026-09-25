"""Define authorize and refusal_text: a Worker's tools ask the Guard before they act.

Roadmap step 10.3 (ADR-0039) wires a Worker's own enforcement points through the Guard's `Enforcer`,
which its Warden hands down on `WorkerContext.enforcer`: `tool_invocation` (a tool must be held as
`tool:<name>` before it runs, and the HTTP tool's `net:<host>`), `session_outside_scratch` (a read
outside scratch needs `fs:read:<path>`; the Capping gate's allowlist refusals are recorded here too)
and `question_routing` (the `ask` tool needs `question:human`). Every tool asks the same way: the
Worker is the principal (its own id, under the policy role its assignment names), its own set is
what it holds, and the Cell it runs on is the context. `authorize` builds that request and returns
the Enforcer's decision (a refusal is already a `guard.denied` row by then); `refusal_text` turns a
refusal into the one-line result a model reads, so a refused call is never a silent no-op and never
an exception; every such line starts with `GUARD_REFUSAL_PREFIX`, the fixed token the Drone's
outcome records read a Guard refusal by. `floor_refusal_text` (roadmap step 10.3a) asks the
Guard's floors alone, for an action whose held set the Capping gate checks (a command's `exec`, a
write's `fs:write`) or already passed `authorize` (the HTTP tool's resolved addresses): a floor
refuses whatever the Worker holds, so the Hive's own state is never reached through those tools.

Fits into the Hive:
    Layer 4 (roles that do the work), inside `hivemind.workers.tools`. Called by
    `hivemind.workers.tools.registry`, `.http`, `.ask`, `.session` and `.proposals`. Calls into
    `hivemind.guard` and `hivemind.workers.tools.registry` (ToolInvocation) only.

Key invariants:
    - The principal is always the Worker itself and the held set always its own
      `WorkerContext.capabilities`: a tool can never borrow its Warden's wider set.
    - `authorize` and `floor_refusal_text` record nothing on an allow (the action's own event
      does that) and never raise on a refusal.

See Also:
    - docs/adr/0039-capability-model-attenuation-and-enforcement-points.md for the points.
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
from hivemind.guard.net import IPAddress

if TYPE_CHECKING:
    # Only for the annotations: the registry imports this module back for its own check.
    from hivemind.workers.tools.registry import ToolInvocation

# Every refusal line starts with this fixed, machine-produced token (never model prose), so a
# reader such as `hivemind.workers.roles.drone.outcome.records` can classify it by exact prefix.
GUARD_REFUSAL_PREFIX = "refused by the Guard "

__all__ = ["GUARD_REFUSAL_PREFIX", "authorize", "floor_refusal_text", "refusal_text"]


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
    return await invocation.ctx.enforcer.check(_request(invocation, point, needed, None))


async def floor_refusal_text(
    invocation: ToolInvocation,
    point: EnforcementPoint,
    needed: Capability,
    resolved: tuple[IPAddress, ...] | None = None,
) -> str | None:
    """Ask the Guard's floors alone about an action; return the refusal line, or None.

    Args:
        invocation: This attempt's context and assignment.
        point: The enforcement point the calling tool is passing.
        needed: The one capability the action needs.
        resolved: Every address the needed `net` host resolved to, when the tool resolved it;
            the floors judge each one (`PolicyContext.resolved_addresses`).

    Returns:
        The refusal line (`refusal_text`) when a floor refuses, already `guard.denied` on the
        trail; None when no floor does, which leaves the held set to its own check.
    """
    request = _request(invocation, point, needed, resolved)
    decision = await invocation.ctx.enforcer.check_floors(request)
    return None if decision is None else refusal_text(decision)


def refusal_text(decision: PolicyDecision) -> str:
    """Render a refusal as the tool-result line a model reads: what was refused, and why.

    Args:
        decision: A refused PolicyDecision.

    Returns:
        One sentence naming the Guard's own reason; the action did not happen.
    """
    return f"{GUARD_REFUSAL_PREFIX}({decision.rule}): {decision.reason} Nothing was done."


def _request(
    invocation: ToolInvocation,
    point: EnforcementPoint,
    needed: Capability,
    resolved: tuple[IPAddress, ...] | None,
) -> PolicyRequest:
    """Build this Worker's request: itself, its own set, and its Cell (plus what it resolved)."""
    ctx = invocation.ctx
    # Tier inheritance (roadmap step 10.3b): a task runs at its Cell's tier, so the Cell's tier is
    # also the task's bound tier for every check its tools make.
    context = PolicyContext(
        comb_shield=ctx.cell.comb_shield,
        access_level=ctx.cell.access_level,
        bound_tier=ctx.cell.comb_shield,
        resolved_addresses=resolved,
    )
    return PolicyRequest(
        principal=worker_principal(ctx.worker_id, invocation.assignment.role),
        point=point,
        needed=needed,
        held=ctx.capabilities,
        context=context,
    )
