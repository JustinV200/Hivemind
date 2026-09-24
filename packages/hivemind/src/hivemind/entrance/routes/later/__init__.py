"""Hold the resources the contract names before the phases that fill them: tools, honey, swarm.

ADR-0032: a Landing Board resource whose subsystem is not built yet answers ``501`` with the
roadmap phase that fills it, so the contract names every resource from the start. One module per
resource (``tools``, phase 9; ``honey``, phase 7; ``swarm``, phase 11), sharing the row shape
``unbuilt`` builds; each is replaced by its real reads when its phase lands.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered
    in ``hivemind.entrance.routes.registry``. Calls into the gate's row types and the view models.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every row here is a READ answering 501.

See Also:
    - hivemind.entrance.models.views.unbuilt for the answer.

Public API:
    - TOOL_ROUTES, TOOLS_PHASE: the tools resource (tools).
    - HONEY_ROUTES, HONEY_PHASE: the honey resource (honey).
    - SWARM_ROUTES, SWARM_PHASE: the swarm resource (swarm).
    - not_built_row, NOT_IMPLEMENTED: the shared row shape (unbuilt).
"""

from hivemind.entrance.routes.later.honey import HONEY_PHASE
from hivemind.entrance.routes.later.honey import ROUTES as HONEY_ROUTES
from hivemind.entrance.routes.later.swarm import ROUTES as SWARM_ROUTES
from hivemind.entrance.routes.later.swarm import SWARM_PHASE
from hivemind.entrance.routes.later.tools import ROUTES as TOOL_ROUTES
from hivemind.entrance.routes.later.tools import TOOLS_PHASE
from hivemind.entrance.routes.later.unbuilt import NOT_IMPLEMENTED, not_built_row

__all__ = [
    "HONEY_PHASE",
    "HONEY_ROUTES",
    "NOT_IMPLEMENTED",
    "SWARM_PHASE",
    "SWARM_ROUTES",
    "TOOLS_PHASE",
    "TOOL_ROUTES",
    "not_built_row",
]
