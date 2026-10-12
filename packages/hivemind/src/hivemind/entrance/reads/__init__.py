"""Hold the Entrance's read side: views joined from several stores, and the paged trail read.

Reads never change state, so the Hive Entrance reads the Hive's stores and the Queen's live tables
directly (ADR-0040), never through her door. Most read routes are one store call and a shaping
function; what needs more lives here, shared by the read routes and the live views: ``census``
joins each Cell's and Warden's standing from the Queen's links and pulse, the Virtual Cell
lifecycle, the trail, the Brood Chamber, the telemetry board and the Forage ledger; ``trail``
pages the Pheromone Trail from a cursor; ``llm`` judges each provider from the Queen's own
bookkeeping without probing it; ``honey`` turns a device into the reader the Honey browser filters
by and gathers a folder, an item or a search for it (roadmap 12.9); ``screens`` holds the reads
behind the Observation Hive's aggregated screens (roadmap 12.1), one module per screen.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance``. Called by
    ``hivemind.entrance.routes.hive`` and ``hivemind.entrance.streams.views``. Calls into the
    Hive's stores through ``hivemind.entrance.gate.HiveReads`` and the view models.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing here writes: every function only reads.

See Also:
    - hivemind.entrance.gate.reads for what is read.

Public API:
    - cell_views, cell_view, warden_views, PLACED, EVENT_LOOKBACK: the census (census).
    - read_trail, TrailRead: the paged trail (trail).
    - llm_view: the providers, their health and the slot bindings (llm).
    - honey_viewer, HoneyViewer, locate_folder, locate_item, scope_of, read_folder, read_item,
      read_search, require_honey: the Honey browser's reads for one viewer (honey).
    - screens: read_summary, read_fleet, read_cell_page, read_attendant, read_thoughts,
      read_capping, read_cost and read_metrics, each in its own module (a sub-package).
"""

from hivemind.entrance.reads.census import (
    EVENT_LOOKBACK,
    PLACED,
    cell_view,
    cell_views,
    warden_views,
)
from hivemind.entrance.reads.honey import (
    HoneyViewer,
    honey_viewer,
    locate_folder,
    locate_item,
    read_folder,
    read_item,
    read_search,
    require_honey,
    scope_of,
)
from hivemind.entrance.reads.llm import llm_view
from hivemind.entrance.reads.trail import TrailRead, read_trail

__all__ = [
    "EVENT_LOOKBACK",
    "PLACED",
    "HoneyViewer",
    "TrailRead",
    "cell_view",
    "cell_views",
    "honey_viewer",
    "llm_view",
    "locate_folder",
    "locate_item",
    "read_folder",
    "read_item",
    "read_search",
    "read_trail",
    "require_honey",
    "scope_of",
    "warden_views",
]
