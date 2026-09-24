"""Name the tools resource in the contract before the Comb Registry lands (phase 9): a 501.

The tools a bee may use are promoted into the Comb Registry through the Quarantine Comb (roadmap
phase 9). Until then ``GET /v1/tools`` answers ``501`` naming that phase (ADR-0032), so the
contract lists the resource now and a client learns when it arrives.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.later``.
    Registered in the route table. Calls into the not-built row helper only.

Key invariants:
    - Answers 501 and touches nothing until phase 9 replaces it with the registry's reads.

See Also:
    - .claude/roadmap.md phase 9 for the Royal Jelly Lab and the Comb Registry.
"""

from __future__ import annotations

from hivemind.entrance.gate.spec import RouteSpec
from hivemind.entrance.routes.later.unbuilt import not_built_row

TOOLS_PHASE = 9  # The Royal Jelly Lab and the Comb Registry.

__all__ = ["ROUTES", "TOOLS_PHASE"]

ROUTES: tuple[RouteSpec, ...] = (
    not_built_row("tools", TOOLS_PHASE, "the Comb Registry's promoted tools"),
)
