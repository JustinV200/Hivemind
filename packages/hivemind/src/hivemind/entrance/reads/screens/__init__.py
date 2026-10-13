"""Hold the Entrance's reads for the Observation Hive's screens: one module per screen.

The Observation Hive's screen models are composed by pure builders in ``hivemind.observation``
(codingrules 8.3: pure core, effectful edges), and the Entrance does the reading, directly from the
Hive's stores and the Queen's live tables (ADR-0040). Each module here reads what one screen needs
through ``HiveReads`` and hands plain records to its builder: ``summary`` (the strip), ``fleet``,
``cell_page``, ``attendant``, ``thoughts``, ``capping``, ``cost`` and ``metrics``; ``gather`` holds
the reads several of them share (tasks, Alarms, telemetry, episodes, Cell Wax, hosting, leases).

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.reads``. Called by
    ``hivemind.entrance.routes.screens``. Calls into the Hive's stores through
    ``hivemind.entrance.gate.HiveReads``, the census beside it and the observation face's builders.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing here writes: every function only reads.

See Also:
    - hivemind.entrance.reads.census for the Cell and Warden views these screens join.
    - hivemind.observation.api for the builders and models.

Public API:
    - read_summary, read_fleet, read_cell_page, read_attendant, read_thoughts, read_capping,
      read_cost, read_metrics: the screens' reads.
"""

from hivemind.entrance.reads.screens.attendant import read_attendant
from hivemind.entrance.reads.screens.capping import read_capping
from hivemind.entrance.reads.screens.cell_page import read_cell_page
from hivemind.entrance.reads.screens.cost import read_cost
from hivemind.entrance.reads.screens.fleet import read_fleet
from hivemind.entrance.reads.screens.metrics import read_metrics
from hivemind.entrance.reads.screens.summary import read_summary
from hivemind.entrance.reads.screens.thoughts import read_thoughts

__all__ = [
    "read_attendant",
    "read_capping",
    "read_cell_page",
    "read_cost",
    "read_fleet",
    "read_metrics",
    "read_summary",
    "read_thoughts",
]
