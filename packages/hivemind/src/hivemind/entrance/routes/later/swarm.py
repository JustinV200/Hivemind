"""Name the swarm resource in the contract before the Swarm lands (phase 11): a 501.

The Swarm is the operator's own devices enrolled as Real Cells through a Pollen Packet (roadmap
phase 11). Until then ``GET /v1/swarm`` answers ``501`` naming that phase (ADR-0040), so the
contract lists the resource now.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.later``.
    Registered in the route table. Calls into the not-built row helper only.

Key invariants:
    - Answers 501 and touches nothing until phase 11 replaces it with the Swarm's reads.

See Also:
    - .claude/roadmap.md phase 11 for the Swarm.
"""

from __future__ import annotations

from hivemind.entrance.gate.spec import RouteSpec
from hivemind.entrance.routes.later.unbuilt import not_built_row

SWARM_PHASE = 11  # The Swarm: devices enrolled as Real Cells.

__all__ = ["ROUTES", "SWARM_PHASE"]

ROUTES: tuple[RouteSpec, ...] = (
    not_built_row("swarm", SWARM_PHASE, "the Swarm's enrolled devices"),
)
