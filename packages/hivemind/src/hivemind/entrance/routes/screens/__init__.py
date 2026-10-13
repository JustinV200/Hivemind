"""Hold the Landing Board's routes for the Observation Hive's aggregated screens and metrics.

Roadmap 12.1: one route per screen model that joins several resources, each a ``GET`` on both
listeners behind ``observe`` (``observe:thoughts`` and C2 for the thoughts): ``summary`` (the
strip), ``fleet``, ``cell_page`` (``/v1/cells/{id}/page``), ``attendant``, ``thoughts``,
``capping``, ``cost`` and ``metrics`` (Prometheus text). They read the Hive's stores directly
through ``hivemind.entrance.reads.screens`` and answer with the models of
``hivemind.observation``; nothing here writes, so the Observation Hive's one write path stays the
chat.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered in
    ``hivemind.entrance.routes.registry``. Calls into the screens' reads and the observation face.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every row here is a READ: no mutating route joins the table through this package.

See Also:
    - hivemind.entrance.routes.README for the route table.

Public API:
    - SUMMARY_ROUTES, FLEET_ROUTES, CELL_PAGE_ROUTES, ATTENDANT_ROUTES, THOUGHTS_ROUTES,
      CAPPING_ROUTES, COST_ROUTES, METRICS_ROUTES: each screen's rows.
"""

from hivemind.entrance.routes.screens.attendant import ROUTES as ATTENDANT_ROUTES
from hivemind.entrance.routes.screens.capping import ROUTES as CAPPING_ROUTES
from hivemind.entrance.routes.screens.cell_page import ROUTES as CELL_PAGE_ROUTES
from hivemind.entrance.routes.screens.cost import ROUTES as COST_ROUTES
from hivemind.entrance.routes.screens.fleet import ROUTES as FLEET_ROUTES
from hivemind.entrance.routes.screens.metrics import ROUTES as METRICS_ROUTES
from hivemind.entrance.routes.screens.summary import ROUTES as SUMMARY_ROUTES
from hivemind.entrance.routes.screens.thoughts import ROUTES as THOUGHTS_ROUTES

__all__ = [
    "ATTENDANT_ROUTES",
    "CAPPING_ROUTES",
    "CELL_PAGE_ROUTES",
    "COST_ROUTES",
    "FLEET_ROUTES",
    "METRICS_ROUTES",
    "SUMMARY_ROUTES",
    "THOUGHTS_ROUTES",
]
