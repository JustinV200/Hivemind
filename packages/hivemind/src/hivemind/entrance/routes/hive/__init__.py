"""Hold the Hive's read routes: tasks, Cells, Wardens, Forage, episodes, the trail and the LLM.

Codingrules 8.11: everything the Observation Hive shows besides the chat is a live read, over the
streams and these routes. One module per resource (ADR-0032), each declaring its rows: every row
is a GET on both listeners, reads the Hive's stores or the Queen's live tables directly (never
through the Queen's door), needs ``observe`` (``observe:thoughts`` for episode records) and also
``honey:clearance:c2`` wherever it answers a human's words (a task's brief, an episode record).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.routes``. Registered
    in ``hivemind.entrance.routes.registry``. Calls into ``hivemind.entrance.reads``, the Hive's
    stores through the gate's ``HiveReads`` and the view models.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Every row here is a READ: no mutating route joins the table through this package.

See Also:
    - hivemind.entrance.routes.README for the route table.

Public API:
    - TASK_ROUTES, CELL_ROUTES, WARDEN_ROUTES, FORAGE_ROUTES, EPISODE_ROUTES, TRAIL_ROUTES,
      LLM_ROUTES: each resource's rows (tasks, cells, wardens, forage, episodes, trail, llm).
"""

from hivemind.entrance.routes.hive.cells import ROUTES as CELL_ROUTES
from hivemind.entrance.routes.hive.episodes import ROUTES as EPISODE_ROUTES
from hivemind.entrance.routes.hive.forage import ROUTES as FORAGE_ROUTES
from hivemind.entrance.routes.hive.llm import ROUTES as LLM_ROUTES
from hivemind.entrance.routes.hive.tasks import ROUTES as TASK_ROUTES
from hivemind.entrance.routes.hive.trail import ROUTES as TRAIL_ROUTES
from hivemind.entrance.routes.hive.wardens import ROUTES as WARDEN_ROUTES

__all__ = [
    "CELL_ROUTES",
    "EPISODE_ROUTES",
    "FORAGE_ROUTES",
    "LLM_ROUTES",
    "TASK_ROUTES",
    "TRAIL_ROUTES",
    "WARDEN_ROUTES",
]
