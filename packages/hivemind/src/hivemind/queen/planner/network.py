"""Define owner_named_needs: keep only the network scopes the goal's owner asked for.

A planned task's `network_scopes` become `net:` grants for its Worker
(`hivemind.workers.capabilities`), and on the operator's own path nothing above the Warden's
`net:*` narrows them. The planner reads retrieved Honey as well as the goal, so a scope it plans
could come from injected text. A scope survives only when the goal text names it, or the goal's
own ceiling holds that exact `net:` grant (the only way to keep `*`); every other scope is dropped
before the task exists.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package's
    planner sub-package. Called by `hivemind.queen.planner.plan`. Calls into `hivemind.cell`
    (TaskNeeds) only.

Key invariants:
    - Only ever removes scopes, never adds one.
    - Retrieved content can never justify a scope: only the goal text and its ceiling are read.

See Also:
    - hivemind.workers.capabilities for where a kept scope becomes a `net:` grant.
"""

from __future__ import annotations

import re

from hivemind.cell import TaskNeeds

# The owner says "localhost" or an address; TaskNeeds always spells it `loopback`.
LOOPBACK_SCOPE = "loopback"
LOOPBACK_NAMES = (LOOPBACK_SCOPE, "localhost", "127.0.0.1", "::1")

__all__ = ["owner_named_needs"]


def owner_named_needs(
    needs: TaskNeeds, goal: str, capabilities: tuple[str, ...] | None
) -> TaskNeeds:
    """Return `needs` without any network scope the goal's owner did not ask for.

    Args:
        needs: One planned task's needs, as the model wrote them.
        goal: The goal text, as the human stated it.
        capabilities: The goal's ceiling; an exact `net:<scope>` in it justifies that scope.

    Returns:
        `needs` unchanged when every scope is named, otherwise a copy with only the named ones.
    """
    held = set(capabilities or ())
    kept = tuple(
        scope
        for scope in needs.network_scopes
        if f"net:{scope}" in held or _goal_names(goal, scope)
    )
    if kept == needs.network_scopes:
        return needs
    return needs.model_copy(update={"network_scopes": kept})


def _goal_names(goal: str, scope: str) -> bool:
    """Return whether `goal` names `scope` as a whole host, address or network."""
    if scope == "*":
        return False
    names = LOOPBACK_NAMES if scope == LOOPBACK_SCOPE else (scope, scope.removeprefix("*."))
    # Whole-name match only, so "example.com" never justifies "evil-example.com" or the reverse.
    return any(
        re.search(rf"(?<![\w.-]){re.escape(name)}(?!\.?[\w-])", goal, re.IGNORECASE)
        for name in names
    )
