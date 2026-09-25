"""Serve the Observation Hive's read side: its read models now, its read API and metrics later.

The read models (``hivemind.observation.views``) are what every Observation Hive view renders
(codingrules 8.11): Cells, Wardens, tasks, the Forage split, thoughts, the trail, providers and
the live views' frames. The Entrance answers its read routes and streams with them and publishes
them in the OpenAPI document the front end's types are generated from; it imports them from this
face only. The aggregated read API and the metrics come with phase 12. The front end that renders
these views lives separately, in packages/observation-web.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard). Used by ``hivemind.entrance`` (its routes, read
    side and live views), the one same-layer import ADR-0040 lists; never imports the Entrance.
    Calls into the Hive's record types (Brood Chamber, Cells, Forage, memory, the trail, the
    Queen's ledger and mode) and pydantic.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - Nothing in this package writes: every view is a read model (codingrules 8.11's one write
      path is the Queen's inbox, which the Entrance owns).

See Also:
    - .claude/codingrules.md 8.11 for the views these models serve.
    - .claude/roadmap.md phase 12 for the read API and metrics still to come.

Public API:
    - Every name ``hivemind.observation.views`` exports; its face groups them by view.
"""

from hivemind.observation.views import (
    DEFAULT_TRAIL_PAGE,
    MAX_FILTER_CHARS,
    MAX_TRAIL_PAGE,
    MAX_TRAIL_SKIP,
    NOT_BUILT_CODE,
    WORDS_FIELDS,
    CapacityView,
    CellFrame,
    CellList,
    CellMode,
    CellView,
    EpisodeFrame,
    EpisodeList,
    EpisodeView,
    ForageFrame,
    ForageView,
    GrantView,
    HeadroomView,
    LlmView,
    MaskStateName,
    NotBuiltView,
    ProviderHealthView,
    ProviderView,
    ReserveView,
    SlotView,
    TaskBriefView,
    TaskGraphFrame,
    TaskOutcomeView,
    TaskPage,
    TaskView,
    TelemetryFrame,
    TelemetrySampleView,
    TrailCursor,
    TrailEventView,
    TrailFilters,
    TrailFrame,
    TrailPage,
    TrailUsageView,
    WardenList,
    WardenView,
    capacity_view,
    episode_view,
    forage_view,
    grant_view,
    headroom_view,
    reserve_view,
    task_brief,
    task_view,
    telemetry_samples,
    trail_event_view,
)

__all__ = [
    "DEFAULT_TRAIL_PAGE",
    "MAX_FILTER_CHARS",
    "MAX_TRAIL_PAGE",
    "MAX_TRAIL_SKIP",
    "NOT_BUILT_CODE",
    "WORDS_FIELDS",
    "CapacityView",
    "CellFrame",
    "CellList",
    "CellMode",
    "CellView",
    "EpisodeFrame",
    "EpisodeList",
    "EpisodeView",
    "ForageFrame",
    "ForageView",
    "GrantView",
    "HeadroomView",
    "LlmView",
    "MaskStateName",
    "NotBuiltView",
    "ProviderHealthView",
    "ProviderView",
    "ReserveView",
    "SlotView",
    "TaskBriefView",
    "TaskGraphFrame",
    "TaskOutcomeView",
    "TaskPage",
    "TaskView",
    "TelemetryFrame",
    "TelemetrySampleView",
    "TrailCursor",
    "TrailEventView",
    "TrailFilters",
    "TrailFrame",
    "TrailPage",
    "TrailUsageView",
    "WardenList",
    "WardenView",
    "capacity_view",
    "episode_view",
    "forage_view",
    "grant_view",
    "headroom_view",
    "reserve_view",
    "task_brief",
    "task_view",
    "telemetry_samples",
    "trail_event_view",
]
