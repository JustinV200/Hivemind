"""Name the honey resource in the contract before the Honey Store lands (phase 7): a 501.

Honey is the Hive's ripened, retrievable knowledge, browsed by provenance and filtered by the
viewer's ``observe:honey:<scope>`` capabilities and clearance (codingrules 8.11). It arrives with
roadmap phase 7; until then ``GET /v1/honey`` answers ``501`` naming that phase (ADR-0040).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes.later``.
    Registered in the route table. Calls into the not-built row helper only.

Key invariants:
    - Answers 501 and touches nothing until phase 7 replaces it with the Honey browser's reads.

See Also:
    - .claude/roadmap.md phase 7 for the Honey Store.
"""

from __future__ import annotations

from hivemind.entrance.gate.spec import RouteSpec
from hivemind.entrance.routes.later.unbuilt import not_built_row

HONEY_PHASE = 7  # The Honey Store: Nectar, ripening and retrieval.

__all__ = ["HONEY_PHASE", "ROUTES"]

ROUTES: tuple[RouteSpec, ...] = (
    not_built_row("honey", HONEY_PHASE, "the Honey browser over provenance"),
)
