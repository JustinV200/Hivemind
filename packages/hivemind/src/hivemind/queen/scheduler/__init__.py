"""Re-export the Swarming policy: how many fresh Virtual Cells each backend may start now.

The Queen's scheduling policies that sit beside placement rather than inside it. Swarming
(ADR-0063, roadmap step 13.3) is the first: a pure cap on fresh Virtual Cells from queue depth,
the Hive Stand's room, per-backend limits and an hourly cost cap.

Fits into the Hive:
    Layer 6 (the kernel; the only global view; divides Forage), inside the queen package. Read by
    `hivemind.queen.dispatcher.snapshot` and `hivemind.queen.deps`; built from the manifest by
    `hivemind.cli.compose`. Imports nothing from `hivemind`, and `hivemind.llm` is forbidden to it.

Key invariants:
    - This module contains no logic of its own: it is a face over `.swarming` (codingrules 5.2).

See Also:
    - hivemind.queen.scheduler.swarming for the policy and its invariants.
    - docs/adr/0063-swarming-policy-and-cost-caps.md for the decision.

Public API:
    - SwarmingPolicy, BackendLimit: the configured bounds (swarming).
    - SwarmView, BackendLoad: what the dispatcher measured (swarming).
    - SwarmPlan, plan_swarm: the decision (swarming).
"""

from hivemind.queen.scheduler.swarming import (
    BackendLimit,
    BackendLoad,
    SwarmingPolicy,
    SwarmPlan,
    SwarmView,
    plan_swarm,
)

__all__ = ["BackendLimit", "BackendLoad", "SwarmPlan", "SwarmView", "SwarmingPolicy", "plan_swarm"]
