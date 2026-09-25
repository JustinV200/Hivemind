"""Hold the Entrance's read side: views joined from several stores, and the paged trail read.

Reads never change state, so the Hive Entrance reads the Hive's stores and the Queen's live tables
directly (ADR-0040), never through her door. Most read routes are one store call and a shaping
function; what needs more lives here, shared by the read routes and the live views: ``census``
joins each Cell's and Warden's standing from the Queen's links and pulse, the Virtual Cell
lifecycle, the trail, the Brood Chamber, the telemetry board and the Forage ledger; ``trail``
pages the Pheromone Trail from a cursor; ``llm`` judges each provider from the Queen's own
bookkeeping without probing it.

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
"""

from hivemind.entrance.reads.census import (
    EVENT_LOOKBACK,
    PLACED,
    cell_view,
    cell_views,
    warden_views,
)
from hivemind.entrance.reads.llm import llm_view
from hivemind.entrance.reads.trail import TrailRead, read_trail

__all__ = [
    "EVENT_LOOKBACK",
    "PLACED",
    "TrailRead",
    "cell_view",
    "cell_views",
    "llm_view",
    "read_trail",
    "warden_views",
]
