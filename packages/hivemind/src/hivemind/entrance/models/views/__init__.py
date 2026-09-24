"""Hold the Hive's read models: what the Observation Hive's views render, one family per module.

Codingrules 8.11: every Observation Hive view is data-shaped, one pydantic read model the web app
renders through TypeScript types generated from the committed OpenAPI document. Those models are
the Landing Board's own read routes and stream frames, so they live here, beside the Entrance's
other models, rather than in ``hivemind.observation.views``: the layer table makes ``entrance``
and ``observation`` independent siblings (neither may import the other), and the routes the
document is generated from must import the models they answer with. Each view carries only what
its access allows: the observe views hold ids, states, figures and times; anything written from
the human's words (a task's brief, an episode record, a bee's goal line) is its own model behind
``honey:clearance:c2``.

Fits into the Hive:
    Layer 7 (edges: HTTP, terminal, dashboard), inside ``hivemind.entrance.models``. Used by the
    read routes (``hivemind.entrance.routes.hive``, ``.later``), the read side
    (``hivemind.entrance.reads``) and the live views (``hivemind.entrance.streams.views``);
    published in the OpenAPI document. Calls into the Hive's record types and pydantic.

Key invariants:
    - This file holds re-exports and ``__all__`` only.
    - A change here is a contract change: additive within ``/v1/`` (ADR-0034).

See Also:
    - .claude/codingrules.md 8.11 for the views these models serve.

Public API:
    - TaskView, TaskOutcomeView, TaskPage, TaskBriefView, task_view, task_brief: tasks.
    - CellView, CellList, CellMode, MaskStateName: Cells (cells).
    - WardenView, WardenList: Wardens (wardens).
    - ForageView, CapacityView, GrantView, HeadroomView, ReserveView, forage_view, capacity_view,
      grant_view, headroom_view, reserve_view: the Forage ledger (forage).
    - EpisodeView, EpisodeList, episode_view: thoughts (episodes).
    - TrailEventView, TrailUsageView, TrailCursor, TrailPage, TrailFilters, trail_event_view,
      MAX_TRAIL_PAGE, MAX_TRAIL_SKIP, DEFAULT_TRAIL_PAGE, MAX_FILTER_CHARS: the trail (trail).
    - LlmView, ProviderView, ProviderHealthView, SlotView: providers and slots (llm).
    - NotBuiltView, NOT_BUILT_CODE: a resource a later phase fills (unbuilt).
    - TrailFrame, TelemetryFrame, TelemetrySampleView, ForageFrame, TaskGraphFrame, EpisodeFrame,
      CellFrame, telemetry_samples: the live views' frames (frames).
"""

from hivemind.entrance.models.views.cells import CellList, CellMode, CellView, MaskStateName
from hivemind.entrance.models.views.episodes import EpisodeList, EpisodeView, episode_view
from hivemind.entrance.models.views.forage import (
    CapacityView,
    ForageView,
    GrantView,
    HeadroomView,
    ReserveView,
    capacity_view,
    forage_view,
    grant_view,
    headroom_view,
    reserve_view,
)
from hivemind.entrance.models.views.frames import (
    CellFrame,
    EpisodeFrame,
    ForageFrame,
    TaskGraphFrame,
    TelemetryFrame,
    TelemetrySampleView,
    TrailFrame,
    telemetry_samples,
)
from hivemind.entrance.models.views.llm import (
    LlmView,
    ProviderHealthView,
    ProviderView,
    SlotView,
)
from hivemind.entrance.models.views.tasks import (
    TaskBriefView,
    TaskOutcomeView,
    TaskPage,
    TaskView,
    task_brief,
    task_view,
)
from hivemind.entrance.models.views.trail import (
    DEFAULT_TRAIL_PAGE,
    MAX_FILTER_CHARS,
    MAX_TRAIL_PAGE,
    MAX_TRAIL_SKIP,
    TrailCursor,
    TrailEventView,
    TrailFilters,
    TrailPage,
    TrailUsageView,
    trail_event_view,
)
from hivemind.entrance.models.views.unbuilt import NOT_BUILT_CODE, NotBuiltView
from hivemind.entrance.models.views.wardens import WardenList, WardenView

__all__ = [
    "DEFAULT_TRAIL_PAGE",
    "MAX_FILTER_CHARS",
    "MAX_TRAIL_PAGE",
    "MAX_TRAIL_SKIP",
    "NOT_BUILT_CODE",
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
